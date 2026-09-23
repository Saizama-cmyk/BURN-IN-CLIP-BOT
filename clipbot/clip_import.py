"""Repost Twitch's most popular viewer-made clips with Ashvane's edit on top.

Every ``clip_import.interval_h`` the Helix clips API is asked for the most-viewed clips of the
last ``period_h`` — for the top games discovery found and (optionally) for the streamers being
monitored. Clips below ``min_views``, in other languages, longer than ``clip.max_len_s`` or
already imported are skipped. The best ``max_per_run`` are downloaded with (bundled)
streamlink and handed to the normal pipeline as candidates of kind ``popular_clip``: QC →
transcribe → vision + judge (optional, with its own ``min_score``) → copywriter → editor →
scheduler. Posts credit both the streamer and the viewer who made the clip.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from .config import AppPaths, Settings, streamlink_cmd
from .db import Store
from .discovery import Discovery, DiscoveryError
from .models import Candidate, Platform, SpikeEvent, StreamTarget, new_id
from .util import ERR_SNIPPET, CmdTimeout, run_cmd

logger = logging.getLogger("clipbot.clip_import")

KIND = "popular_clip"
CLIP_URL = "https://clips.twitch.tv/{id}"


def rfc3339(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pick_clips(clips: list[dict], settings: Settings, already: Callable[[str], bool]) -> list[dict]:
    """Filter + rank raw Helix clip objects (pure)."""
    ci, langs = settings.clip_import, {x.strip().lower() for x in settings.twitch.languages if x.strip()}
    seen: set[str] = set()
    out = []
    for c in sorted(clips, key=lambda c: -int(c.get("view_count", 0) or 0)):
        cid = str(c.get("id", ""))
        if not cid or cid in seen or already(cid):
            continue
        seen.add(cid)
        if int(c.get("view_count", 0) or 0) < ci.min_views:
            continue
        if float(c.get("duration", 0) or 0) > settings.clip.max_len_s:
            continue
        lang = str(c.get("language", "")).lower()
        if langs and lang and lang not in langs:
            continue
        out.append(c)
    return out[:ci.max_per_run]


def candidate_from_clip(clip: dict, raw_path: Path) -> Candidate:
    login = str(clip.get("broadcaster_name", "")).strip()
    t = StreamTarget(platform=Platform.TWITCH, login=login.lower().replace(" ", ""),
                     display_name=login, category=str(clip.get("game_name", "") or ""),
                     viewers=int(clip.get("view_count", 0) or 0), title=str(clip.get("title", "")),
                     user_id=str(clip.get("broadcaster_id", "")))
    views, creator = int(clip.get("view_count", 0) or 0), str(clip.get("creator_name", ""))
    now = time.time()
    duration = float(clip.get("duration", 0) or 0)
    ev = SpikeEvent(target=t, t_wall=now + duration / 2, kind=KIND, score=float(views),
                    chat_rate=0.0, baseline=0.0, id=new_id(),
                    chat_sample=[f"(Popular Twitch clip: {views:,} views, clipped by {creator}, "
                                 f"titled \"{clip.get('title', '')}\")"])
    return Candidate(id=ev.id, event=ev, raw_path=str(raw_path), start_wall=now,
                     end_wall=now + duration,
                     source={"type": "twitch_clip", "clip_id": clip.get("id"), "clipper": creator,
                             "views": views, "url": clip.get("url", ""),
                             "created_at": clip.get("created_at", "")})


class ClipImporter:
    def __init__(self, settings: Settings, store: Store, discovery: Discovery, paths: AppPaths,
                 launch: Callable[[Candidate], None], targets: Callable[[], list[StreamTarget]]) -> None:
        self.settings = settings
        self.store = store
        self.discovery = discovery
        self.paths = paths
        self.launch = launch
        self.targets = targets
        self.last_run = 0.0
        self.status = "waiting for the first check"
        self.imported = 0
        self._task: asyncio.Task | None = None

    def apply(self, settings: Settings) -> None:
        self.settings = settings

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="clip-import")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            ci = self.settings.clip_import
            try:
                if ci.enabled and time.time() - self.last_run >= ci.interval_h * 3600 \
                        and self.discovery.top_game_ids:
                    await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # keep the importer alive; the traceback is logged
                logger.exception("clip import failed")
            await asyncio.sleep(self.settings.discovery.interval_s)

    async def fetch(self) -> list[dict]:
        ci = self.settings.clip_import
        started = rfc3339(time.time() - ci.period_h * 3600)
        clips: list[dict] = []
        for gid in self.discovery.top_game_ids[:ci.from_top_games]:
            clips += await self.discovery.twitch_clips(game_id=gid, started_at=started,
                                                       first=ci.per_source)
        if ci.from_watched_streamers:
            for t in self.targets():
                if t.platform == Platform.TWITCH and t.user_id:
                    clips += await self.discovery.twitch_clips(broadcaster_id=t.user_id,
                                                               started_at=started,
                                                               first=ci.per_source)
        return clips

    async def download(self, clip: dict) -> Path:
        out = self.paths.work / f"clip_{clip['id']}.mp4"
        url = CLIP_URL.format(id=clip["id"])
        res = await run_cmd([*streamlink_cmd(self.settings), "--loglevel", "warning", "--force",
                             "--output", str(out), url, "best"],
                            self.settings.clip_import.download_timeout_s,
                            grace_s=self.settings.app.shutdown_timeout_s)
        if res.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            out.unlink(missing_ok=True)
            raise RuntimeError(f"download failed: {(res.err_text or res.out_text).strip()[:ERR_SNIPPET]}")
        return out

    async def run_once(self) -> int:
        self.last_run = time.time()
        try:
            raw = await self.fetch()
        except (httpx.HTTPError, DiscoveryError) as exc:
            self.status = f"Twitch clips API error: {exc}"
            logger.warning(self.status)
            return 0
        picked = pick_clips(raw, self.settings,
                            lambda cid: self.store.clip_imported(cid))
        done = 0
        for clip in picked:
            try:
                path = await self.download(clip)
            except (RuntimeError, CmdTimeout, OSError) as exc:
                logger.warning("clip %s: %s", clip.get("id"), exc)
                await asyncio.to_thread(self.store.mark_clip_imported, str(clip["id"]), "")
                continue
            c = candidate_from_clip(clip, path)
            await asyncio.to_thread(self.store.mark_clip_imported, str(clip["id"]), c.id)
            self.launch(c)
            done += 1
        self.imported += done
        self.status = (f"last check {datetime.now().strftime('%H:%M')}: {len(raw)} clips seen, "
                       f"{done} new imported")
        logger.info("clip import: %s", self.status)
        return done

    def snapshot(self) -> dict:
        return {"enabled": self.settings.clip_import.enabled, "status": self.status,
                "imported": self.imported, "last_run": self.last_run}

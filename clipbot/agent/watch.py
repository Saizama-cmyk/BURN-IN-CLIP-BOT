"""Eyes and ears for the phone's assistant: watch a video, look at pictures, keep uploads.

The phone's own model does the thinking. What it cannot do is see or hear, so when it is sent a
video or a picture it asks the PC to turn that into text - what is said, what is on screen, what
a picture shows - and reasons over the text itself.

Videos are fetched the way BURN-IN fetches streams: streamlink resolves Twitch, Kick, YouTube and
the rest to a playable address, and a plain video link goes straight to ffmpeg. Only the first
``assistant.watch_max_s`` seconds are taken. The vision model and Whisper share their GPU lock
with the clip pipeline, so a request from the phone waits its turn rather than colliding with it.
"""
from __future__ import annotations

import logging
import re
import shutil
import time
import uuid
from pathlib import Path

from ..config import ffmpeg_exe, streamlink_cmd
from ..editor import RenderError, probe_media
from ..util import CmdTimeout, run_cmd

logger = logging.getLogger("clipbot.agent.watch")

UPLOAD_ID = re.compile(r"^[a-f0-9]{32}$")
SAFE_EXT = re.compile(r"^\.[A-Za-z0-9]{1,8}$")
SAID_MAX = 8000           # characters of transcript handed back
SCREEN_MAX = 6000         # characters of the vision model's description handed back
MB = 1024 * 1024
ERR_TAIL = 300           # characters of ffmpeg's complaint passed on


class WatchError(RuntimeError):
    """The video could not be fetched or read."""


def uploads_dir(data_dir: Path) -> Path:
    return data_dir / "agent" / "uploads"


def prune_uploads(data_dir: Path, keep_h: float) -> None:
    """Files from the phone are scratch: gone after ``keep_h`` hours."""
    folder = uploads_dir(data_dir)
    if not folder.exists():
        return
    cutoff = time.time() - keep_h * 3600
    for f in folder.iterdir():
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            continue


def new_upload(data_dir: Path, name: str) -> tuple[str, Path]:
    """A fresh id and the path to write the phone's file to. The name only lends its extension."""
    ext = Path(name or "").suffix.lower()
    ext = ext if SAFE_EXT.match(ext) else ".bin"
    folder = uploads_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex
    return uid, folder / f"{uid}{ext}"


def find_upload(data_dir: Path, upload_id: str) -> Path | None:
    if not UPLOAD_ID.match(upload_id or ""):
        return None
    hits = sorted(uploads_dir(data_dir).glob(f"{upload_id}.*"))
    return hits[0] if hits else None


async def _resolve(url: str, settings) -> str:
    """A link ffmpeg can read. streamlink knows the sites; a plain file link is used as is."""
    try:
        res = await run_cmd([*streamlink_cmd(settings), "--stream-url", url, "best"],
                            settings.assistant.watch_timeout_s)
    except CmdTimeout:
        return url
    out = res.out_text.strip().splitlines()
    if res.returncode == 0 and out and out[-1].startswith("http"):
        return out[-1]
    return url


async def _fetch(source: str, dest: Path, settings) -> None:
    ffmpeg = ffmpeg_exe(settings)
    if not ffmpeg:
        raise WatchError("ffmpeg not found (Settings -> Paths)")
    try:
        res = await run_cmd([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                             "-i", source, "-t", str(settings.assistant.watch_max_s),
                             "-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac",
                             str(dest)], settings.assistant.watch_timeout_s)
    except CmdTimeout as exc:
        raise WatchError(f"fetching the video took too long ({exc})") from exc
    if res.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise WatchError(f"could not read that video: {res.err_text.strip()[-ERR_TAIL:]}")


async def watch(ctx, target: str) -> dict:
    """``target`` is a link or ``upload:<id>``. Returns what was said and what was on screen."""
    settings, pipeline = ctx.settings, ctx.pipeline
    work = ctx.paths.work / "watch"
    work.mkdir(parents=True, exist_ok=True)
    clip = work / f"{uuid.uuid4().hex}.mp4"
    target = (target or "").strip()
    try:
        if target.startswith("upload:"):
            found = find_upload(ctx.paths.data, target.split(":", 1)[1])
            if found is None:
                return {"error": "that upload is not on the PC any more - send it again"}
            source = str(found)
        elif target.startswith(("http://", "https://")):
            source = await _resolve(target, settings)
        else:
            return {"error": "send a video link starting with http(s), or attach the video"}
        await _fetch(source, clip, settings)
        info = await probe_media(clip, settings)
        seen = await pipeline.vision.describe(clip, info.duration, None, "", "",
                                              frames=settings.assistant.watch_frames)
        said = (await pipeline.transcriber.transcribe(clip)).text if info.has_audio else ""
        return {"seconds_watched": round(info.duration, 1),
                "said": said[:SAID_MAX] or "(nothing said, or no sound)",
                "on_screen": (seen.text or seen.error or "(vision model unavailable)")[:SCREEN_MAX]}
    except (WatchError, RenderError, OSError, RuntimeError) as exc:
        logger.warning("watch %s failed: %s", target, exc)
        return {"error": str(exc)}
    finally:
        clip.unlink(missing_ok=True)

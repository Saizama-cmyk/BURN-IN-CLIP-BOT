"""Per-platform post copy: titles, descriptions, tags and story-style captions.

After a clip passes review, the local model writes separate copy for YouTube, TikTok,
Instagram, Facebook and Discord in one JSON reply, following each platform's style guide
(``copywriter.*_rules``) and, when analytics has enough data, notes on what has performed
best on the channel. The result is cleaned up to each platform's limits. If anything fails,
posting falls back to the reviewer's title/caption and the Posting templates.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path

import httpx

from .clipability import _extract_json, BadModelOutput
from .config import Settings
from .aiplan import plan
from .models import Candidate
from .safety import Filter
from .util import CmdTimeout
from .vision import extract_frames, frame_times
from .util import ERR_SNIPPET

logger = logging.getLogger("clipbot.copywriter")

PLATFORMS = ("youtube", "tiktok", "instagram", "facebook", "discord")
# API limits (characters) — protocol values, not preferences
YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESC_MAX = 5000
YOUTUBE_TAGS_TOTAL_MAX = 500
CAPTION_MAX = 2200            # TikTok and Instagram
FACEBOOK_TITLE_MAX = 255
DISCORD_MAX = 2000
_TAG_CHARS = re.compile(r"[^\w]", re.UNICODE)


def _tags(raw, limit: int) -> list[str]:
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    out: list[str] = []
    for t in raw if isinstance(raw, list) else []:
        clean = _TAG_CHARS.sub("", str(t).lstrip("#"))
        if clean and clean.lower() not in {x.lower() for x in out}:
            out.append(clean)
    return out[:limit]


def _text(v, limit: int) -> str:
    return str(v or "").strip()[:limit]


def _one_line(v, limit: int) -> str:
    return " ".join(str(v or "").split())[:limit]


def normalize_copy(data: dict, settings: Settings) -> dict:
    """Validate the model's JSON into per-platform dicts; raise BadModelOutput if unusable."""
    cw = settings.copywriter
    out: dict = {"hook": _one_line(data.get("hook"), CAPTION_MAX),
                 "vibe": _one_line(data.get("vibe"), CAPTION_MAX).lower()}
    yt = data.get("youtube") or {}
    if isinstance(yt, dict) and yt.get("title"):
        tags, total = [], 0
        for t in _tags(yt.get("tags"), cw.youtube_tags_max):
            if total + len(t) > YOUTUBE_TAGS_TOTAL_MAX:
                break
            tags.append(t)
            total += len(t)
        out["youtube"] = {"title": _one_line(yt["title"], YOUTUBE_TITLE_MAX),
                          "description": _text(yt.get("description"), YOUTUBE_DESC_MAX),
                          "tags": tags}
    for name in ("tiktok", "instagram"):
        d = data.get(name) or {}
        if isinstance(d, dict) and d.get("caption"):
            out[name] = {"caption": _text(d["caption"], CAPTION_MAX),
                         "hashtags": _tags(d.get("hashtags"), cw.hashtags_max)}
    fb = data.get("facebook") or {}
    if isinstance(fb, dict) and (fb.get("title") or fb.get("description")):
        out["facebook"] = {"title": _one_line(fb.get("title"), FACEBOOK_TITLE_MAX),
                           "description": _text(fb.get("description"), CAPTION_MAX)}
    dc = data.get("discord") or {}
    if isinstance(dc, dict) and dc.get("message"):
        out["discord"] = {"message": _text(dc["message"], DISCORD_MAX)}
    for name in ("youtube", "tiktok", "instagram", "facebook"):      # engagement first comment
        d = data.get(name)
        if name in out and isinstance(d, dict) and d.get("comment"):
            out[name]["comment"] = _one_line(d["comment"], CAPTION_MAX)
    if not any(p in out for p in PLATFORMS):
        raise BadModelOutput("no platform copy in reply")
    return out


def platform_rules(settings: Settings) -> str:
    cw = settings.copywriter
    rules = {"youtube": cw.youtube_rules, "tiktok": cw.tiktok_rules,
             "instagram": cw.instagram_rules, "facebook": cw.facebook_rules,
             "discord": cw.discord_rules}
    return "\n".join(f"- {name.title()}: {rules[name]}" for name in PLATFORMS)


def copy_prompt(settings: Settings) -> str:
    return (settings.copywriter.prompt.replace("{brand}", settings.brand.name)
            .replace("{platform_rules}", platform_rules(settings)))


def clip_brief(c: Candidate, insights: str, chat_lines: int) -> str:
    t, v = c.event.target, c.verdict
    platform = "Twitch" if str(t.platform) == "twitch" else "Kick"
    ev = c.event
    spike_at = ev.t_wall - c.start_wall
    chat = [m for m in (ev.chat_sample or []) if m.strip()][-chat_lines:] if chat_lines else []
    nl = "\n"
    return (f"Streamer: {t.display_name} ({platform})\nCategory: {t.category or 'unknown'}\n"
            f"Stream title: {t.title or '(none)'}\n"
            f"Reviewer's note (internal - never reuse its wording): {v.get('reason', '')}\n"
            f"The spike: {ev.kind} at {spike_at:.1f}s into the cut (chat z {ev.chat_z:.1f}"
            f"{', keywords: ' + ', '.join(ev.keywords_hit) if ev.keywords_hit else ''}).\n\n"
            f"Chat's reaction (only to help you understand the moment - the viewer never sees chat, "
            f"so never mention it):\n{nl.join('- ' + m for m in chat) or '(no chat captured)'}\n\n"
            f"Transcript:\n{c.transcript.strip() or '(no speech)'}\n\n"
            f"On screen:\n{c.visual.strip() or '(no visual description)'}\n\n"
            f"What has worked on this channel:\n{insights.strip() or '(not enough data yet)'}\n")


def narrates(copy: dict, patterns: list[str]) -> str:
    """The first phrase in the titles/captions that describes the clip instead of hooking, or ''."""
    texts = [copy.get("hook", ""), (copy.get("youtube") or {}).get("title", ""),
             (copy.get("tiktok") or {}).get("caption", ""), (copy.get("instagram") or {}).get("caption", "")]
    for pattern in patterns:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            logger.warning("describing phrase %r is not a valid pattern: %s", pattern, exc)
            continue
        for text in texts:
            m = rx.search(text or "")
            if m:
                return m.group(0)
    return ""


class Copywriter:
    def __init__(self, settings: Settings, http: httpx.AsyncClient,
                 gpu_lock: asyncio.Lock | None = None) -> None:
        self.settings = settings
        self.http = http
        self._lock = gpu_lock or asyncio.Lock()
        self.busy = False
        self.last_error = ""

    def apply(self, settings: Settings) -> None:
        self.settings = settings

    vram_gb: float | None = None          # set by the pipeline from nvidia-smi

    def model(self) -> str:
        cw = self.settings.copywriter
        ai = self.settings.ai
        p = plan(self.settings, self.vram_gb)
        if cw.model:
            return cw.model
        return p.judge_model if p.one_model else (ai.vision_model if cw.watch else ai.model)

    def _shares_model(self) -> bool:
        return plan(self.settings, self.vram_gb).one_model or self.model() == self.settings.ai.vision_model

    async def _chat(self, messages: list[dict]) -> str:
        s = self.settings
        seeing = any(m.get("images") for m in messages)
        body = {"model": self.model(), "messages": messages, "format": "json", "stream": False,
                "keep_alive": s.ai.keep_alive,
                "options": {"temperature": s.copywriter.temperature,
                            "num_ctx": s.ai.vision_num_ctx if seeing or self._shares_model() else s.ai.num_ctx}}
        if seeing or self._shares_model():
            # vision models (qwen3-vl) think anyway and return an EMPTY reply under format=json;
            # without it they still answer with a JSON object, which _extract_json pulls out
            body.pop("format")
            if s.ai.vision_disable_thinking:
                body["think"] = False
        url = f"{s.ai.ollama_url.rstrip('/')}/api/chat"
        r = await self.http.post(url, json=body, timeout=s.ai.timeout_s)
        if r.status_code == 400 and "think" in body:             # model without a thinking switch
            body.pop("think")
            r = await self.http.post(url, json=body, timeout=s.ai.timeout_s)
        if r.status_code != 200:
            raise BadModelOutput(f"Ollama HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
        return str(r.json().get("message", {}).get("content", ""))

    async def _frames(self, c: Candidate, video: Path | None) -> list[str]:
        """JPEG frames from the cut (one at the spike) for the writer to watch; [] if unavailable."""
        cw = self.settings.copywriter
        if not (cw.watch and video and video.exists()):
            return []
        duration = max(1.0, c.end_wall - c.start_wall)
        times = frame_times(duration, cw.frames, c.event.t_wall - c.start_wall)
        try:
            frames = await extract_frames(video, times, self.settings)
        except (RuntimeError, CmdTimeout, OSError) as exc:
            logger.warning("copywriter could not grab frames for %s: %s", c.id, exc)
            return []
        return [base64.b64encode(f).decode("ascii") for f in frames]

    async def _rewrite(self, messages: list[dict], raw: str, phrase: str, first: dict) -> dict:
        """Send narrating copy back once; keep the rewrite if it parses, else the first version."""
        s = self.settings
        retry = messages + [{"role": "assistant", "content": raw},
                            {"role": "user", "content": s.copywriter.rewrite_prompt.replace("{phrase}", phrase)}]
        try:
            again = Filter(s).clean(normalize_copy(_extract_json(await self._chat(retry)), s))
        except (BadModelOutput, json.JSONDecodeError, httpx.HTTPError) as exc:
            logger.info("copy rewrite failed (%s); keeping the first version", exc)
            return first
        still = narrates(again, s.copywriter.narration)
        logger.info("copy rewritten (was %r)%s", phrase, f"; still describes: {still!r}" if still else "")
        return again

    async def write(self, c: Candidate, insights: str = "", video: Path | None = None) -> dict:
        """Per-platform copy for a passed clip, or {} (fallback to templates) on failure."""
        s = self.settings
        if not s.copywriter.enabled:
            return {}
        user = {"role": "user", "content": clip_brief(c, insights, s.copywriter.chat_lines)}
        images = await self._frames(c, video)
        if images:
            user["images"] = images
        messages = [{"role": "system", "content": copy_prompt(s)}, user]
        async with self._lock:
            self.busy = True
            try:
                for attempt in range(s.ai.retries + 1):
                    try:
                        raw = await self._chat(messages)
                        result = Filter(s).clean(normalize_copy(_extract_json(raw), s))
                        phrase = narrates(result, s.copywriter.narration)
                        if phrase:
                            result = await self._rewrite(messages, raw, phrase, result)
                        self.last_error = ""
                        return result
                    except (BadModelOutput, json.JSONDecodeError) as exc:
                        self.last_error = str(exc)
                        logger.warning("copywriter attempt %d for %s: %s", attempt + 1, c.id, exc)
                    except httpx.HTTPError as exc:
                        self.last_error = f"Ollama unreachable: {exc}"
                        logger.warning("copywriter for %s: %s", c.id, self.last_error)
                        break
            finally:
                self.busy = False
        return {}

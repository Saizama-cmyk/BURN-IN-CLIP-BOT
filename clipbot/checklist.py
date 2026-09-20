"""First-run setup checklist: what's missing and which Settings field fixes it."""
from __future__ import annotations

import logging
import sys

import httpx

from .config import Settings, ffmpeg_exe, ffprobe_exe, is_frozen, streamlink_cmd
from .util import CmdTimeout, run_cmd

logger = logging.getLogger("clipbot.checklist")


def _item(id_: str, label: str, ok: bool, detail: str, field: str, required: bool) -> dict:
    return {"id": id_, "label": label, "ok": ok, "detail": detail, "field": field,
            "required": required}


async def _runs(args: list[str], timeout_s: float) -> tuple[bool, str]:
    try:
        res = await run_cmd(args, timeout_s)
    except (OSError, CmdTimeout) as exc:
        return False, str(exc)
    first = (res.out_text or res.err_text).strip().splitlines()
    return res.returncode == 0, first[0] if first else f"exit code {res.returncode}"


async def run_checklist(settings: Settings, http: httpx.AsyncClient) -> dict:
    s = settings
    timeout = s.capture.probe_timeout_s
    items: list[dict] = []

    ff = ffmpeg_exe(s)
    ok, detail = await _runs([ff, "-version"], timeout) if ff else (False, "not found on PATH")
    items.append(_item("ffmpeg", "ffmpeg installed", ok, detail if ok else
                       f"{detail} — install with: winget install Gyan.FFmpeg", "paths.ffmpeg", True))
    fp = ffprobe_exe(s)
    ok, detail = await _runs([fp, "-version"], timeout) if fp else (False, "not found on PATH")
    items.append(_item("ffprobe", "ffprobe installed", ok, detail, "paths.ffprobe", True))

    sl = streamlink_cmd(s)
    if is_frozen() and not s.paths.streamlink:
        ok, detail = True, "bundled with BURN-IN"
    else:
        ok, detail = await _runs([*sl, "--version"], timeout)
    items.append(_item("streamlink", "streamlink available", ok, detail, "paths.streamlink", True))

    base = s.ai.ollama_url.rstrip("/")
    try:
        r = await http.get(f"{base}/api/tags", timeout=s.discovery.http_timeout_s)
        models = [m.get("name") for m in r.json().get("models", [])]
        items.append(_item("ollama", "Ollama reachable", True, f"{len(models)} model(s) at {base}",
                           "ai.ollama_url", True))
        has = s.ai.model in models
        items.append(_item("model", f"Model {s.ai.model} installed", has,
                           "ready" if has else f"run: ollama pull {s.ai.model}", "ai.model", True))
        if s.ai.vision_enabled:
            vis = s.ai.vision_model in models
            items.append(_item("vision", f"Vision model {s.ai.vision_model} installed", vis,
                               "ready — the judge can see the video" if vis else
                               f"optional: run  ollama pull {s.ai.vision_model}  so the judge can "
                               "see the video (clips are judged on audio + chat until then)",
                               "ai.vision_model", False))
    except (httpx.HTTPError, ValueError) as exc:
        items.append(_item("ollama", "Ollama reachable", False,
                           f"cannot reach {base} ({type(exc).__name__}) — start Ollama",
                           "ai.ollama_url", True))

    tw = bool(s.twitch.client_id and s.twitch.client_secret) and s.twitch.enabled
    kk = bool(s.kick.client_id and s.kick.client_secret) and s.kick.enabled
    items.append(_item("twitch", "Twitch developer app keys", tw,
                       "set" if tw else "needed to discover Twitch streams", "twitch.client_id",
                       not kk))
    items.append(_item("kick", "Kick developer app keys", kk,
                       "set" if kk else "needed to discover Kick streams", "kick.client_id",
                       not tw))

    posting = {"youtube": ("accounts.youtube_client_id", s.accounts.youtube_client_id),
               "tiktok": ("accounts.tiktok_client_key", s.accounts.tiktok_client_key),
               "instagram": ("accounts.instagram_token", s.accounts.instagram_token),
               "facebook": ("accounts.facebook_page_token", s.accounts.facebook_page_token),
               "discord": ("accounts.discord_webhook", s.accounts.discord_webhook)}
    any_post = any(v for _, v in posting.values())
    items.append(_item("posting", "At least one posting account", any_post,
                       "set" if any_post else "optional: clips are still saved to the clips folder",
                       "accounts.discord_webhook", False))

    missing_required = [i for i in items if i["required"] and not i["ok"]]
    return {"ok": not missing_required, "items": items,
            "python": sys.version.split()[0], "frozen": is_frozen()}

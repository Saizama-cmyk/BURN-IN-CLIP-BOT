"""What the assistant is allowed to do.

Every tool is a small, named function over the running app: read the state, read clips, read the
log, publish a clip, pause or resume. Nothing here can delete a clip,
change a password, spend money or touch a file outside the app's own folders - an assistant
driven by a language model should not be one wrong sentence away from damage.

Tools are described to the model in the JSON shape Ollama expects, and dispatched by name.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger("clipbot.agent.tools")

CLIPS_MAX = 25
LOG_MAX = 40
TEXT_MAX = 4000
CLIPS_DEFAULT = 10        # a sensible page of clips when the model does not say
CHAT_SAMPLE_MAX = 20      # chat lines shown with one clip
LOG_DEFAULT = 20          # log lines when the model does not say
LOG_BYTES = 200_000       # read this much of the end of the log, then filter


@dataclass
class Tool:
    name: str
    description: str
    schema: dict                      # JSON schema for the arguments
    run: Callable[..., Awaitable[Any]]
    writes: bool = False              # changes something, so it may need permission

    def spec(self) -> dict:
        """The shape Ollama's tool calling expects."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.schema}}


def no_args() -> dict:
    return {"type": "object", "properties": {}}


def obj(**props: dict) -> dict:
    """A JSON schema object; a property whose schema has ``required: True`` is required."""
    required = [k for k, v in props.items() if v.pop("required", False)]
    out = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


def build(ctx) -> dict[str, Tool]:
    """Wire the tools to the running app. ``ctx`` is the app: pipeline, settings, paths."""
    pipeline = ctx.pipeline

    async def status() -> dict:
        snap = await pipeline.snapshot()
        return {k: snap.get(k) for k in ("status", "manual_paused", "failsafe", "stages",
                                         "backlog", "gpu", "version") if k in snap}

    async def streams() -> list[dict]:
        snap = await pipeline.snapshot()
        return [{"name": m.get("display_name"), "platform": m.get("platform"),
                 "category": m.get("category"), "viewers": m.get("viewers"),
                 "chat": m.get("chat")} for m in (snap.get("monitors") or [])]

    async def recent_clips(limit: int = CLIPS_DEFAULT, stage: str = "") -> list[dict]:
        rows = await asyncio.to_thread(pipeline.store.recent_candidates, CLIPS_MAX)
        out = []
        for d in rows:
            if stage and d.get("stage") != stage:
                continue
            target = (d.get("event") or {}).get("target") or {}
            verdict = d.get("verdict") or {}
            out.append({"id": d.get("id"), "streamer": target.get("display_name"),
                        "stage": d.get("stage"), "score": verdict.get("score"),
                        "title": verdict.get("title"),
                        "why": verdict.get("reason") or d.get("error")})
            if len(out) >= max(1, min(int(limit), CLIPS_MAX)):
                break
        return out

    async def clip_detail(clip_id: str) -> dict:
        c = await asyncio.to_thread(pipeline.store.get_candidate, clip_id)
        if c is None:
            return {"error": f"no clip with id {clip_id}"}
        verdict = c.verdict or {}
        return {"id": c.id, "streamer": c.event.target.display_name,
                "category": c.event.target.category, "stage": str(c.stage),
                "score": verdict.get("score"), "title": verdict.get("title"),
                "why": verdict.get("reason") or c.error, "posts": c.post_copy,
                "transcript": (c.transcript or "")[:TEXT_MAX],
                "what_was_on_screen": (c.visual or "")[:TEXT_MAX],
                "chat_at_the_time": (c.event.chat_sample or [])[:CHAT_SAMPLE_MAX]}

    async def read_log(lines: int = LOG_DEFAULT, containing: str = "") -> list[str]:
        from .. import syslog
        raw = await asyncio.to_thread(syslog.tail_lines, ctx.paths.logs / "clipbot.log", LOG_BYTES)
        rows = [f"{e['t']} {e['level']} {e['src']}: {e['msg']}" for e in syslog.parse(raw)]
        if containing:
            rows = [r for r in rows if containing.lower() in r.lower()]
        return rows[-max(1, min(int(lines), LOG_MAX)):]

    async def disk_report() -> dict:
        from ..media import ollama_models_dir, storage_report
        return await asyncio.to_thread(storage_report, ctx.paths, ollama_models_dir())

    async def settings_read(section: str) -> dict:
        from ..config import masked_dump
        return masked_dump(ctx.settings).get(section, {"error": f"no section called {section}"})

    async def pause() -> dict:
        await pipeline.pause()
        return {"paused": True}

    async def resume() -> dict:
        await pipeline.resume()
        return {"paused": False}

    async def publish_clip(clip_id: str) -> dict:
        from ..publishers import PLATFORMS
        return await pipeline.publish_now(clip_id, list(PLATFORMS))


    tools = [
        Tool("status", "How BURN-IN is doing right now: running or paused, queue, trouble, GPU.",
             no_args(), status),
        Tool("streams", "The streams being watched, with viewers and how fast chat is moving.",
             no_args(), streams),
        Tool("recent_clips", "The newest clips with their stage, score and title.",
             obj(limit={"type": "integer", "description": "how many, up to 25"},
                 stage={"type": "string", "description": "only this stage, e.g. scheduled"}),
             recent_clips),
        Tool("clip_detail", "Everything about one clip: its posts, transcript, what was on screen "
             "and why it was judged the way it was.",
             obj(clip_id={"type": "string", "required": True}), clip_detail),
        Tool("read_log", "Recent log lines, optionally only those containing some text.",
             obj(lines={"type": "integer"}, containing={"type": "string"}), read_log),
        Tool("disk_report", "Space used by clips, buffers and work files, and what is free.",
             no_args(), disk_report),
        Tool("settings_read", "Read one section of the settings, with secrets hidden.",
             obj(section={"type": "string", "required": True}), settings_read),
        Tool("pause", "Stop capturing streams for now.", no_args(), pause, writes=True),
        Tool("resume", "Start capturing streams again.", no_args(), resume, writes=True),
        Tool("publish_clip", "Post a finished clip everywhere right now, skipping the schedule.",
             obj(clip_id={"type": "string", "required": True}), publish_clip, writes=True),
    ]
    return {t.name: t for t in tools}

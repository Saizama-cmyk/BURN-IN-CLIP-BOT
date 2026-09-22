"""What the assistant is allowed to do.

Every tool is a small, named function over the running app: read the state, read clips, read the
log, publish a clip someone already approved, pause or resume. Nothing here can delete a clip,
change a password, spend money or touch a file outside the app's own folders - an assistant
driven by a language model should not be one wrong sentence away from damage.

Tools are described to the model in the JSON shape Ollama expects, and dispatched by name.
"""
from __future__ import annotations

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
LOG_SCAN = 4              # read this many times the asked-for lines, then filter


@dataclass
class Tool:
    name: str
    description: str
    schema: dict                      # JSON schema for the arguments
    run: Callable[..., Awaitable[Any]]
    writes: bool = False              # changes something, so it needs permission

    def spec(self) -> dict:
        """The shape Ollama's tool calling expects."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.schema}}


def _no_args() -> dict:
    return {"type": "object", "properties": {}}


def build(ctx) -> dict[str, Tool]:
    """Wire the tools to the running app. ``ctx`` is the ClipBotApp."""
    pipeline = ctx.pipeline

    async def status() -> dict:
        snap = await pipeline.snapshot()
        backlog = snap.get("backlog") or {}
        return {
            "status": snap.get("status"),
            "paused": snap.get("manual_paused"),
            "watching": (snap.get("counts") or {}).get("watching"),
            "clips": (snap.get("counts") or {}).get("clips"),
            "posted": (snap.get("counts") or {}).get("posted"),
            "rejected": (snap.get("counts") or {}).get("rejected"),
            "queue": backlog.get("in_flight"),
            "queue_pressure": round(backlog.get("pressure") or 0, 2),
            "catching_up": backlog.get("failsafe"),
            "trouble": snap.get("trouble") or [],
            "gpu": snap.get("gpu"),
        }

    async def streams() -> list[dict]:
        snap = await pipeline.snapshot()
        return [{"name": s.get("name"), "platform": s.get("key", "").split(":")[0],
                 "category": s.get("category"), "viewers": s.get("viewers"),
                 "chat_per_second": s.get("rate"), "chat_z": s.get("z")}
                for s in (snap.get("streams") or [])]

    async def recent_clips(limit: int = CLIPS_DEFAULT, stage: str = "") -> list[dict]:
        import asyncio
        rows = await asyncio.to_thread(pipeline.store.recent_candidates, CLIPS_MAX)
        out = []
        for c in rows:
            verdict = c.verdict or {}
            if stage and c.stage != stage:
                continue
            out.append({
                "id": c.id,
                "streamer": c.event.target.display_name,
                "stage": str(c.stage),
                "score": verdict.get("score"),
                "title": verdict.get("title"),
                "why": verdict.get("reason") or c.error,
                "created": c.created_at,
            })
            if len(out) >= max(1, min(limit, CLIPS_MAX)):
                break
        return out

    async def clip_detail(clip_id: str) -> dict:
        import asyncio
        c = await asyncio.to_thread(pipeline.store.candidate, clip_id)
        if c is None:
            return {"error": f"no clip with id {clip_id}"}
        verdict = c.verdict or {}
        return {
            "id": c.id, "streamer": c.event.target.display_name,
            "category": c.event.target.category, "stage": str(c.stage),
            "score": verdict.get("score"), "title": verdict.get("title"),
            "caption": verdict.get("caption"), "hashtags": verdict.get("hashtags"),
            "why": verdict.get("reason") or c.error,
            "transcript": (c.transcript or "")[:TEXT_MAX],
            "what_was_on_screen": (c.visual or "")[:TEXT_MAX],
            "chat_at_the_time": (c.event.chat_sample or [])[:CHAT_SAMPLE_MAX],
        }

    async def read_log(lines: int = LOG_DEFAULT, containing: str = "") -> list[str]:
        from .. import syslog
        entries = syslog.parse(syslog.tail_lines(ctx.paths.logs / "clipbot.log", LOG_MAX * LOG_SCAN))
        rows = [f"{e['t']} {e['level']} {e['src']}: {e['msg']}" for e in entries]
        if containing:
            rows = [r for r in rows if containing.lower() in r.lower()]
        return rows[-max(1, min(lines, LOG_MAX)):]

    async def disk_report() -> dict:
        from ..media import storage_report
        return await storage_report(ctx.settings, ctx.paths)

    async def settings_read(section: str) -> dict:
        from ..config import masked_dump
        data = masked_dump(ctx.settings)
        return data.get(section, {"error": f"no settings section called {section}"})

    async def pause(reason: str = "") -> dict:
        await pipeline.set_manual_paused(True)
        logger.info("assistant paused capture%s", f": {reason}" if reason else "")
        return {"paused": True}

    async def resume() -> dict:
        await pipeline.set_manual_paused(False)
        logger.info("assistant resumed capture")
        return {"paused": False}

    async def publish(clip_id: str) -> dict:
        import asyncio
        c = await asyncio.to_thread(pipeline.store.candidate, clip_id)
        if c is None:
            return {"error": f"no clip with id {clip_id}"}
        await pipeline.publish_now(c)
        logger.info("assistant published %s", clip_id)
        return {"publishing": clip_id, "title": (c.verdict or {}).get("title")}

    tools = [
        Tool("status", "How BURN-IN is doing right now: running or paused, queue, trouble, GPU.",
             _no_args(), status),
        Tool("streams", "The streams being watched, with viewers and how fast chat is moving.",
             _no_args(), streams),
        Tool("recent_clips", "The most recent clips with their stage, score and title.",
             {"type": "object", "properties": {
                 "limit": {"type": "integer", "description": "how many, up to 25"},
                 "stage": {"type": "string",
                           "description": "only this stage: scheduled, posted, rejected"}}},
             recent_clips),
        Tool("clip_detail", "Everything known about one clip: copy, transcript, what was on "
             "screen, and why the judge decided what it did.",
             {"type": "object", "properties": {"clip_id": {"type": "string"}},
              "required": ["clip_id"]}, clip_detail),
        Tool("read_log", "Recent log lines, optionally only those containing some text.",
             {"type": "object", "properties": {
                 "lines": {"type": "integer"},
                 "containing": {"type": "string"}}}, read_log),
        Tool("disk_report", "Space used by clips, buffers and work files, and what is free.",
             _no_args(), disk_report),
        Tool("settings_read", "Read one section of the settings, with secrets masked.",
             {"type": "object", "properties": {"section": {"type": "string"}},
              "required": ["section"]}, settings_read),
        Tool("pause", "Stop capturing for now.",
             {"type": "object", "properties": {"reason": {"type": "string"}}}, pause, writes=True),
        Tool("resume", "Start capturing again.", _no_args(), resume, writes=True),
        Tool("publish_clip", "Post a clip that is ready, skipping the schedule.",
             {"type": "object", "properties": {"clip_id": {"type": "string"}},
              "required": ["clip_id"]}, publish, writes=True),
    ]
    return {t.name: t for t in tools}

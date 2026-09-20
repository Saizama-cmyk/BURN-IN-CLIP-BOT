"""Desktop pet: the data it shows and where it sits.

The pet window (static/pet.html) is an always-on-top transparent pywebview window owned by
the desktop shell. It reads ``pet_state`` through /api/pet/state with a per-run key, so it
keeps working while the dashboard itself is locked. Everything it knows comes from the
pipeline snapshot: no extra work happens in the engine for it.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .config import Settings, atomic_write

logger = logging.getLogger("clipbot.pets")

HOUR_S = 3600
GRAPH_HOURS = 12        # clips-per-hour bars on the hover card
TOP_STREAMS = 3         # trending list length
RECENT_MAX = 12         # recent clips/posts handed to the pet
PASSED = ("edit", "scheduled", "posted")


def _stream(m: dict) -> dict:
    c = m.get("chat") or {}
    cap = m.get("capture") or {}
    return {"key": m.get("key"), "name": m.get("display_name") or m.get("login"),
            "platform": m.get("platform"), "category": m.get("category") or "",
            "viewers": m.get("viewers") or 0, "rate": round(float(c.get("rate", 0.0)), 2),
            "baseline": round(float(c.get("baseline", 0.0)), 2),
            "z": round(float(c.get("z", 0.0)), 2), "warm": bool(c.get("warm")),
            "spark": c.get("spark") or [], "live": bool(cap.get("buffered_s"))}


def pet_state(snap: dict, settings: Settings, now: float | None = None,
              *, history: dict[str, list[int]] | None = None) -> dict:
    """The small, stable view of BURN-IN the pet needs (pure; unit-tested)."""
    now = time.time() if now is None else now
    counts = (snap.get("stages") or {}).get("counts") or {}
    samples = snap.get("samples") or []
    platforms = (snap.get("scheduler") or {}).get("platforms") or {}
    streams = sorted((_stream(m) for m in snap.get("monitors") or []),
                     key=lambda s: (s["warm"], s["z"], s["viewers"]), reverse=True)

    hours = [0] * GRAPH_HOURS
    passed_hours = [0] * GRAPH_HOURS
    recent = []
    for c in samples:
        age = now - float(c.get("created_at") or 0)
        stage = str(c.get("stage", ""))
        if 0 <= age < GRAPH_HOURS * HOUR_S:
            slot = GRAPH_HOURS - 1 - int(age // HOUR_S)
            hours[slot] += 1
            if stage in PASSED:
                passed_hours[slot] += 1
        ev = c.get("event") or {}
        tgt = ev.get("target") or {}
        v = c.get("verdict") or {}
        recent.append({"id": c.get("id"), "stage": stage, "created_at": c.get("created_at"),
                       "streamer": tgt.get("display_name") or tgt.get("login") or "",
                       "kind": ev.get("kind", ""), "score": v.get("score"),
                       "title": v.get("title") or ""})

    posts = [{"platform": p.get("platform"), "at": p.get("posted_at"), "ok": p.get("status") == "ok"}
             for p in snap.get("posts") or []]
    posted_24h = {k: int(v.get("posted_24h") or 0) for k, v in platforms.items() if v.get("enabled")}
    ai, chat = snap.get("ai") or {}, snap.get("chat") or {}
    trouble = [t for t in (
        ai.get("error") and f"AI judge: {ai['error']}",
        snap.get("status") == "failsafe" and "Failsafe: capture paused while the queue catches up",
        snap.get("status") == "disk" and "Low disk space: capture paused",
    ) if t]
    p = settings.pets
    return {
        "status": snap.get("status", "idle"), "manual_paused": bool(snap.get("manual_paused")),
        "backlog": snap.get("backlog") or {}, "gpu": snap.get("gpu"),
        "counts": {"clips": sum(int(counts.get(k, 0)) for k in PASSED),
                   "rejected": int(counts.get("rejected", 0)), "failed": int(counts.get("failed", 0)),
                   "posted": sum(posted_24h.values()), "watching": len(streams),
                   "spikes": int((snap.get("spikes") or {}).get("total", 0))},
        "streams": streams[:TOP_STREAMS],
        "hours": history["hours"] if history is not None else hours,
        "passed_hours": history["passed_hours"] if history is not None else passed_hours,
        "posted_by_platform": posted_24h,
        "recent": recent[:RECENT_MAX], "posts": posts[:RECENT_MAX],
        "chat_connected": bool((chat.get("twitch") or {}).get("connected")
                               or (chat.get("kick") or {}).get("connected")),
        "trouble": trouble,
        "pet": p.model_dump(),
    }


class PetPlace:
    """Remembers where the pet was left (data/pet.json)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("pet position unreadable (%s); starting fresh", exc)
            return {}

    def save(self, x: int, y: int) -> None:
        atomic_write(self.path, json.dumps({"x": int(x), "y": int(y)}))

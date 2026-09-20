"""The System panel behind the status card: what the status means, recent events, issues and
analytics, built from BURN-IN's own log file and database (pure functions, unit-tested)."""
from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path

from .config import Settings

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+(\w+)\s+([\w.]+): (.*)$")
_TOKENS = re.compile(r"(token|access_token|client_secret|key)=[^\s&)]+", re.I)
EVENT_SOURCES = ("clipbot.detector", "clipbot.pipeline", "clipbot.scheduler", "clipbot.discovery",
                 "clipbot.analytics", "clipbot.clip_import", "clipbot.main", "clipbot.app",
                 "clipbot.editor", "clipbot.publishers")
QUIET = ("httpx", "uvicorn.access")
HOUR_S = 3600
DAY_HOURS = 24
GROUP_KEY_MAX = 160     # issues are grouped by their first chars (numbers blanked)
TOP_GROUPS = 12
TOP_REASONS = 8


def tail_lines(path: Path, max_bytes: int) -> list[str]:
    """Last ``max_bytes`` of the log (cheap on big files)."""
    if not path.exists():
        return []
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read().decode("utf-8", "replace")
    lines = data.splitlines()
    return lines[1:] if size > max_bytes else lines        # first line may be cut


def parse(lines: list[str]) -> list[dict]:
    """Log lines -> entries; traceback/continuation lines attach to the entry above."""
    out: list[dict] = []
    for line in lines:
        m = _LINE.match(line)
        if m:
            when, level, src, msg = m.groups()
            out.append({"t": when, "level": level, "src": src, "msg": _TOKENS.sub(r"\1=…", msg)})
        elif out and line.strip():
            out[-1].setdefault("detail", []).append(_TOKENS.sub(r"\1=…", line))
    return out


def split(entries: list[dict], limit: int) -> dict:
    events = [e for e in entries if e["level"] == "INFO" and e["src"].startswith(EVENT_SOURCES)]
    issues = [e for e in entries if e["level"] in ("WARNING", "ERROR", "CRITICAL")
              and not e["src"].startswith(QUIET)]
    grouped = Counter(re.sub(r"\d+(\.\d+)?", "#", f'{e["src"]}: {e["msg"]}')[:GROUP_KEY_MAX] for e in issues)
    return {"events": events[-limit:][::-1], "issues": issues[-limit:][::-1],
            "issue_groups": [{"what": k, "count": n} for k, n in grouped.most_common(TOP_GROUPS)]}


def explain(snap: dict, settings: Settings) -> dict:
    """Plain-language meaning of the current status, with what to do about it."""
    status, b = snap.get("status", "idle"), snap.get("backlog") or {}
    pct = round(float(b.get("pressure", 0)) * 100)
    if status == "failsafe":
        return {"title": "Failsafe: catching up",
                "why": f"{b.get('in_flight')} clips are waiting to be processed (capacity "
                       f"{b.get('capacity')}, {pct}% full). At {round(b.get('pause_at', 0) * 100)}% "
                       f"BURN-IN stops capturing new streams so your PC isn't overwhelmed, and "
                       f"starts again at {round(b.get('resume_at', 0) * 100)}%.",
                "tips": ["Keep AI → One model for everything on, so Ollama never swaps models.",
                         "Watch fewer streams (Twitch/Kick → Capture slots) or raise Detector → "
                         "Chat z threshold so fewer moments get cut.",
                         "Use the GPU encoder (Editor → Encoder: h264_nvenc) to render faster.",
                         "Close other GPU apps (games, ComfyUI) while BURN-IN runs."]}
    if status == "paused":
        return {"title": "Paused", "why": "You paused capture. Clips already cut keep processing.",
                "tips": ["Press Resume capture to start watching streams again."]}
    if status == "disk":
        return {"title": "Low disk space", "why": "Capture paused because the buffer drive is "
                f"below {settings.app.min_free_gb} GB free.",
                "tips": ["Free some space or move the buffer folder (System → Storage)."]}
    if status == "idle":
        return {"title": "Standby", "why": "Nothing is being watched right now.",
                "tips": ["Check the Setup tab: platform keys, discovery settings, or all your "
                         "forced streamers may be offline."]}
    return {"title": "On air", "why": f"Watching {len(snap.get('monitors') or [])} streams; "
            f"backlog {pct}% full.", "tips": []}


def analytics(samples: list[dict], snap: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    day = [c for c in samples if now - float(c.get("created_at") or 0) < DAY_HOURS * HOUR_S]
    stages = Counter(str(c.get("stage")) for c in day)
    passed = sum(stages[k] for k in ("edit", "scheduled", "posted"))
    judged = passed + stages["rejected"]
    reasons = Counter(((c.get("verdict") or {}).get("category") or (c.get("error") or "").split(":")[0]
                       or "unknown") for c in day if str(c.get("stage")) == "rejected")
    per_hour = Counter(int((now - float(c.get("created_at") or 0)) // HOUR_S) for c in day)
    platforms = ((snap.get("scheduler") or {}).get("platforms") or {})
    return {"cut_24h": len(day), "passed_24h": passed, "rejected_24h": stages["rejected"],
            "failed_24h": stages["failed"], "pass_rate": round(passed / judged * 100) if judged else None,
            "stages": dict(stages), "reject_reasons": reasons.most_common(TOP_REASONS),
            "per_hour": [per_hour.get(h, 0) for h in reversed(range(DAY_HOURS))],
            "posted_24h": {k: v.get("posted_24h", 0) for k, v in platforms.items() if v.get("enabled")},
            "spikes_session": (snap.get("spikes") or {}).get("total", 0),
            "ignored_session": (snap.get("spikes") or {}).get("ignored", 0)}

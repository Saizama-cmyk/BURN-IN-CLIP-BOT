"""Move BURN-IN's data when you pick a new folder.

Choosing a new location on the Storage page updates the setting and records a pending move
in ``<profile home>/pending_moves.json``. Nothing is moved while BURN-IN is running (the
database and capture files are open); the move happens at the next start, before anything
opens them. Files that already exist at the destination are never overwritten.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from .config import AppPaths, Settings, atomic_write, home_dir

logger = logging.getLogger("clipbot.storage")

GB = 1024 ** 3
KINDS = {"data": "data_dir", "buffer": "buffer_dir", "work": "work_dir", "clips": "clips_dir",
         "logs": "logs_dir", "models": "models_dir"}
# never moved with the data folder: these belong to the profile / app, not the data
_STAY = {"settings.json", "profiles.json", "sessions.json", "pending_moves.json", "profiles",
         "webview"}
DATA_FILES = ("clipbot.db", "clipbot.db-wal", "clipbot.db-shm", "tokens.json", "analytics.json",
              "facecams.json")


def pending_path() -> Path:
    return home_dir() / "pending_moves.json"


def current_folder(paths: AppPaths, kind: str) -> Path | None:
    return {"data": paths.data, "buffer": paths.buffer, "work": paths.work, "clips": paths.clips,
            "logs": paths.logs, "models": paths.models}.get(kind)


def queue_move(kind: str, src: Path, dest: Path) -> None:
    moves = load_pending()
    moves = [m for m in moves if m["kind"] != kind] + [{"kind": kind, "from": str(src),
                                                        "to": str(dest)}]
    atomic_write(pending_path(), json.dumps(moves, indent=2))


def load_pending() -> list[dict]:
    p = pending_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("pending moves file unreadable: %s", exc)
        return []


def _move_children(src: Path, dest: Path, only: tuple[str, ...] | None = None) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    moved = 0
    for child in list(src.iterdir()):
        if child.name in _STAY or (only is not None and child.name not in only):
            continue
        target = dest / child.name
        if target.exists():
            logger.warning("not moving %s: %s already exists", child, target)
            continue
        shutil.move(str(child), str(target))
        moved += 1
    return moved


def apply_pending(settings: Settings) -> list[str]:
    """Run queued moves (call at start-up, before the database or captures open)."""
    moves = load_pending()
    if not moves:
        return []
    notes = []
    for m in moves:
        src, dest = Path(m["from"]), Path(m["to"])
        if not src.exists() or src.resolve() == dest.resolve():
            continue
        try:
            if m["kind"] == "data":
                # only BURN-IN's own data files, plus sub-folders that follow the data folder
                follow = tuple(name for name, attr in (("buffer", "buffer_dir"), ("work", "work_dir"),
                                                       ("clips", "clips_dir"), ("logs", "logs_dir"))
                               if not getattr(settings.app, attr))
                n = _move_children(src, dest, DATA_FILES + follow)
            else:
                n = _move_children(src, dest)
            notes.append(f"moved {n} item(s) of {m['kind']} to {dest}")
            logger.info(notes[-1])
            if not any(src.iterdir()) and src != home_dir():
                src.rmdir()
        except OSError as exc:
            notes.append(f"could not move {m['kind']}: {exc}")
            logger.error(notes[-1])
    pending_path().unlink(missing_ok=True)
    return notes


def free_gb(path: Path) -> float:
    """Free space on the drive holding ``path``.

    Returns 0 when the folder cannot be reached at all - an unplugged USB stick, a network
    share that is down, a drive letter that no longer exists. A drive with no room and a drive
    that is not there both mean the same thing to the caller: do not write here."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(path).free / GB
    except OSError as exc:
        logger.debug("cannot use %s: %s", path, exc)
        return 0.0


def clip_drives(settings: Settings, paths: AppPaths) -> list[Path]:
    """Every folder clips may be written to, in the order they should be tried: the main one
    first, then each drive added on the Storage page."""
    out = [paths.clips]
    for entry in settings.app.overflow_dirs:
        entry = entry.strip()
        if not entry:
            continue
        folder = Path(os.path.expandvars(entry))
        if folder not in out:
            out.append(folder)
    return out


def clips_target(settings: Settings, paths: AppPaths) -> Path:
    """Pick the drive to write the next clip to.

    The drives you list are a pool, not a chain: the first one with room wins, and any that is
    missing or full is skipped. That is what makes pulling a USB stick harmless - it simply
    stops being chosen, and the next clip lands on a drive that is actually there.

    When none has the free space you asked for, the roomiest reachable drive is used rather than
    insisting on the main one: a little tight beats writing to a folder that is not mounted. Only
    if nothing at all can be reached does it fall back to the main folder, so the caller always
    gets a path and the failure surfaces as one clear error instead of a missing attribute."""
    want = settings.app.overflow_free_gb
    space = [(folder, free_gb(folder)) for folder in clip_drives(settings, paths)]

    for folder, free in space:
        if free >= want:
            if folder != paths.clips:
                logger.info("clips going to %s (%.0f GB free; earlier drives are full or gone)",
                            folder, free)
            return folder

    reachable = [(folder, free) for folder, free in space if free > 0]
    if reachable:
        folder, free = max(reachable, key=lambda pair: pair[1])
        logger.warning("every clip drive is under %.0f GB free; using the roomiest, %s (%.1f GB)",
                       want, folder, free)
        return folder

    logger.error("no clip drive can be reached; still writing to %s", paths.clips)
    return paths.clips

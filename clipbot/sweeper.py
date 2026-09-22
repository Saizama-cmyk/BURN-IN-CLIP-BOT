"""Reclaim disk space without losing anything that still matters.

Order matters: work files first (they are rebuildable), then buffers for streams nobody is
watching any more, then the oldest segments of live buffers. Finished clips are never touched -
they are the product.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from .config import AppPaths, Settings

logger = logging.getLogger("clipbot.sweeper")

GB = 1024 ** 3
HOUR_S = 3600.0
SEGMENT_GLOB = "seg_*.ts"


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _remove(path: Path) -> int:
    """Delete a file or folder, returning the bytes reclaimed (0 if it was in use)."""
    freed = 0
    try:
        if path.is_dir():
            freed = sum(_size(p) for p in path.rglob("*") if p.is_file())
            shutil.rmtree(path, ignore_errors=True)
        else:
            freed = _size(path)
            path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("could not remove %s: %s", path, exc)
        return 0
    return freed


def stale_work(paths: AppPaths, keep_h: float, now: float | None = None) -> list[Path]:
    """Work files older than ``keep_h`` hours: raw cuts, concat lists, extracted frames."""
    now = now or time.time()
    if not paths.work.exists():
        return []
    cutoff = now - keep_h * HOUR_S
    out = []
    for child in paths.work.iterdir():
        try:
            if child.stat().st_mtime < cutoff:
                out.append(child)
        except OSError:
            continue
    return out


def dead_buffers(paths: AppPaths, live_keys: set[str], idle_h: float,
                 now: float | None = None) -> list[Path]:
    """Buffer folders for streams that are no longer watched and have stopped growing."""
    now = now or time.time()
    if not paths.buffer.exists():
        return []
    cutoff = now - idle_h * HOUR_S
    out = []
    for child in paths.buffer.iterdir():
        if not child.is_dir() or child.name in live_keys:
            continue
        newest = max((_mtime(p) for p in child.glob(SEGMENT_GLOB)), default=0.0)
        if newest < cutoff:
            out.append(child)
    return out


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def oldest_segments(paths: AppPaths, want_bytes: int) -> list[Path]:
    """Oldest buffer segments across every stream, enough to free ``want_bytes``."""
    segs = []
    if not paths.buffer.exists():
        return []
    for folder in paths.buffer.iterdir():
        if folder.is_dir():
            segs += [(p, _mtime(p), _size(p)) for p in folder.glob(SEGMENT_GLOB)]
    segs.sort(key=lambda row: row[1])
    out, freed = [], 0
    for path, _when, size in segs:
        if freed >= want_bytes:
            break
        out.append(path)
        freed += size
    return out


def free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def sweep(settings: Settings, paths: AppPaths, live_keys: set[str],
          want_gb: float = 0.0) -> int:
    """Tidy up, and if ``want_gb`` is set keep going until that much is free. Returns bytes freed.

    Called on a timer, and again by the disk guard before it gives up and pauses capture."""
    freed = 0
    for path in stale_work(paths, settings.app.work_keep_h):
        freed += _remove(path)
    for folder in dead_buffers(paths, live_keys, settings.app.work_keep_h):
        freed += _remove(folder)
    if freed:
        logger.info("tidied up %.1f GB of work files and dead buffers", freed / GB)
    if not want_gb:
        return freed

    missing = int(want_gb * GB) - free_bytes(paths.buffer)
    if missing <= 0:
        return freed
    dropped = 0
    for path in oldest_segments(paths, missing):
        dropped += _remove(path)
    if dropped:
        freed += dropped
        logger.warning("disk was nearly full: dropped %.1f GB of the oldest buffered video",
                       dropped / GB)
    return freed

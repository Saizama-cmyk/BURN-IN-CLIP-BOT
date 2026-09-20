"""Preview images: clip posters, the frames the vision model looked at, live stream thumbnails,
and storage accounting for the Storage page.

Everything here is a small JPEG made with ffmpeg and cached under the work folder:
  <work>/posters/<id>.jpg         poster for a clip (finished clip, else the raw cut)
  <work>/frames/<id>/<n>.jpg      frames the vision model saw (written by vision.py)
  <work>/thumbs/<key>.jpg         newest frame of a stream being captured
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path

from .config import AppPaths, Settings, ffmpeg_exe
from .util import CmdTimeout, run_cmd

logger = logging.getLogger("clipbot.media")

JPEG_PIX_FMT = "yuvj420p"      # streams tagged full-range make the mjpeg encoder refuse

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def safe_name(key: str) -> str:
    return _SAFE.sub("_", key)


async def grab_jpeg(src: Path, out: Path, at_s: float, width: int, settings: Settings) -> Path | None:
    """Write one JPEG frame of ``src`` at ``at_s`` seconds, scaled to ``width``."""
    ffmpeg = ffmpeg_exe(settings)
    if not ffmpeg or not src.exists():
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.jpg")
    try:
        res = await run_cmd([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                             "-ss", f"{max(0.0, at_s):.2f}", "-i", str(src), "-frames:v", "1",
                             "-vf", f"scale={width}:-2,format={JPEG_PIX_FMT}",
                             "-q:v", str(settings.ai.vision_jpeg_quality),
                             str(tmp)], settings.capture.probe_timeout_s)
    except CmdTimeout as exc:
        logger.debug("thumbnail of %s timed out: %s", src.name, exc)
        return None
    if res.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        if at_s > 0:              # seek landed past the end (trimmed clip): take the first frame
            logger.debug("no frame at %.1fs in %s; retrying at 0s", at_s, src.name)
            return await grab_jpeg(src, out, 0.0, width, settings)
        return None
    os.replace(tmp, out)
    return out


async def remux_segment(src: Path, cache_dir: Path, settings: Settings) -> Path | None:
    """Buffer segment (.ts) -> fragmented MP4 the browser can append (stream copy, cached).
    Old cached files are dropped as the buffer rolls past them."""
    out = cache_dir / f"{src.stem}.mp4"
    if out.exists():
        return out
    ffmpeg = ffmpeg_exe(settings)
    if not ffmpeg:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.mp4")
    try:
        res = await run_cmd([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src),
                             "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-bsf:a", "aac_adtstoasc",
                             "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                             "-f", "mp4", str(tmp)], settings.capture.probe_timeout_s)
    except CmdTimeout as exc:
        logger.debug("remux of %s timed out: %s", src.name, exc)
        return None
    if res.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return None
    os.replace(tmp, out)
    horizon = time.time() - settings.capture.buffer_s
    for old in cache_dir.glob("*.mp4"):
        if old.stat().st_mtime < horizon:
            old.unlink(missing_ok=True)
    return out


def poster_path(paths: AppPaths, cid: str) -> Path:
    return paths.work / "posters" / f"{cid}.jpg"


def frames_dir(paths: AppPaths, cid: str) -> Path:
    return paths.work / "frames" / cid


def thumb_path(paths: AppPaths, key: str) -> Path:
    return paths.work / "thumbs" / f"{safe_name(key)}.jpg"


async def poster_for(video: Path, cid: str, at_s: float, settings: Settings,
                     paths: AppPaths) -> Path | None:
    out = poster_path(paths, cid)
    if out.exists() and out.stat().st_mtime >= video.stat().st_mtime:
        return out
    return await grab_jpeg(video, out, at_s, settings.clip.poster_width, settings)


async def stream_thumb(segment: Path, key: str, settings: Settings, paths: AppPaths) -> Path | None:
    """Newest frame of a capture, re-grabbed at most every ``capture.thumb_max_age_s``."""
    out = thumb_path(paths, key)
    if out.exists() and time.time() - out.stat().st_mtime < settings.capture.thumb_max_age_s:
        return out
    return await grab_jpeg(segment, out, 0.0, settings.capture.thumb_width, settings)


def delete_candidate_media(paths: AppPaths, cid: str) -> None:
    poster_path(paths, cid).unlink(missing_ok=True)
    shutil.rmtree(frames_dir(paths, cid), ignore_errors=True)


# --------------------------------------------------------------------------- storage page
def folder_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                continue
    return total


def storage_report(paths: AppPaths, ollama_models: Path | None) -> dict:
    """Bytes used per folder plus free space on each drive involved (blocking: call in a thread)."""
    folders = {"clips": paths.clips, "buffer": paths.buffer, "work": paths.work, "logs": paths.logs}
    if paths.models:
        folders["models"] = paths.models
    items = []
    for name, p in folders.items():
        items.append({"name": name, "path": str(p), "bytes": folder_size(p)})
    items.append({"name": "database", "path": str(paths.db),
                  "bytes": paths.db.stat().st_size if paths.db.exists() else 0})
    drives = {}
    for it in items:
        anchor = Path(it["path"]).anchor or it["path"]
        if anchor not in drives:
            try:
                u = shutil.disk_usage(anchor)
                drives[anchor] = {"drive": anchor, "total": u.total, "free": u.free}
            except OSError:
                continue
    ollama = None
    if ollama_models and ollama_models.exists():
        ollama = {"path": str(ollama_models), "bytes": folder_size(ollama_models)}
    return {"folders": items, "drives": list(drives.values()), "ollama": ollama}


def ollama_models_dir() -> Path:
    """Where Ollama keeps its models (it reads OLLAMA_MODELS, default ~/.ollama/models)."""
    env = os.environ.get("OLLAMA_MODELS")
    return Path(env) if env else Path.home() / ".ollama" / "models"


async def free_bytes(path: Path) -> int | None:
    try:
        return (await asyncio.to_thread(shutil.disk_usage, path)).free
    except OSError:
        return None

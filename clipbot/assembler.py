"""Cut a raw clip out of a capture buffer and quality-check it.

``assemble`` waits (≤ ``clip.segment_wait_s``) until the buffer covers t + post_s, concat-demuxes
the back-to-back segments overlapping [t − pre_s, t + post_s] and encodes H.264/AAC
``<work>/<id>_raw.mp4``. ``quality_check`` runs one ffmpeg pass (blackdetect + volumedetect)
to reject bad captures: missing video/audio, too short, mostly black, or silent.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .capture import Capture, Segment, contiguous_run
from .config import Settings, ffmpeg_exe
from .editor import video_codec_args
from .models import Candidate, SpikeEvent
from .util import ERR_SNIPPET, run_cmd

logger = logging.getLogger("clipbot.assembler")

_DUR_RE = re.compile(r"Duration:\s*(?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)")
_BLACK_RE = re.compile(r"black_duration:\s*([\d.]+)")
_MEAN_RE = re.compile(r"mean_volume:\s*(-?[\d.]+|-inf)\s*dB")
_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*?: Video:")
_AUDIO_RE = re.compile(r"Stream #\d+:\d+.*?: Audio:")


class AssembleError(RuntimeError):
    pass


@dataclass
class QCResult:
    ok: bool
    reason: str
    duration: float
    black_frac: float
    mean_db: float | None
    has_video: bool
    has_audio: bool


def concat_list(segments: list[Segment]) -> str:
    lines = ["ffconcat version 1.0"]
    for seg in segments:
        p = seg.path.resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{p}'")
    return "\n".join(lines) + "\n"


def plan_cut(segments: list[Segment], t: float, pre_s: float, post_s: float,
             max_gap_s: float) -> tuple[list[Segment], float, float, float]:
    """Choose segments and return (segments, offset into the concat, duration, wall start)."""
    run = contiguous_run(segments, t, max_gap_s)
    if not run:
        raise AssembleError("no buffered video around the spike")
    t0 = max(t - pre_s, run[0].start)
    t1 = min(t + post_s, run[-1].end)
    used = [s for s in run if s.end > t0 and s.start < t1]
    if not used or t1 <= t0:
        raise AssembleError("buffer does not cover the spike window")
    return used, t0 - used[0].start, t1 - t0, t0


async def wait_for_coverage(capture: Capture, until: float, wait_s: float,
                            poll_s: float) -> list[Segment]:
    deadline = time.time() + wait_s
    while True:
        segs = await asyncio.to_thread(capture.segments)
        if segs and segs[-1].end >= until:
            return segs
        if time.time() >= deadline:
            return segs
        await asyncio.sleep(poll_s)


async def assemble(event: SpikeEvent, capture: Capture, settings: Settings,
                   work_dir: Path) -> Candidate:
    s = settings
    ffmpeg = ffmpeg_exe(s)
    if not ffmpeg:
        raise AssembleError("ffmpeg not found (Settings → Paths)")
    t = event.t_wall
    segs = await wait_for_coverage(capture, t + s.clip.post_s, s.clip.segment_wait_s,
                                   s.capture.segment_s)
    used, offset, duration, t0 = plan_cut(segs, t, s.clip.pre_s, s.clip.post_s,
                                          s.capture.segment_s)
    work_dir.mkdir(parents=True, exist_ok=True)
    list_path = work_dir / f"{event.id}_concat.txt"
    out = work_dir / f"{event.id}_raw.mp4"
    used = [seg for seg in used if seg.path.exists()]        # pruned again while we planned?
    if not used:
        raise AssembleError("the buffer was pruned before the cut could run")
    await asyncio.to_thread(list_path.write_text, concat_list(used), "utf-8")
    try:
        res = await run_cmd([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "concat", "-safe", "0", "-ss", f"{offset:.3f}", "-t", f"{duration:.3f}",
            "-i", str(list_path),
            "-map", "0:v:0?", "-map", "0:a:0?",
            *video_codec_args(s.edit.encoder, s.clip.raw_preset, s.edit.nvenc_preset, s.clip.raw_crf), "-c:a", "aac", "-b:a", f"{s.edit.audio_bitrate_k}k",
            "-af", "aresample=async=1", "-movflags", "+faststart", str(out)],
            s.edit.render_timeout_s, grace_s=s.app.shutdown_timeout_s)
    finally:
        list_path.unlink(missing_ok=True)
    if res.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        raise AssembleError(f"cut failed: {res.err_text.strip()[-ERR_SNIPPET:] or res.returncode}")
    return Candidate(id=event.id, event=event, raw_path=str(out), start_wall=t0,
                     end_wall=t0 + duration)


def parse_qc(stderr: str) -> tuple[float, float, float | None, bool, bool]:
    m = _DUR_RE.search(stderr)
    duration = (int(m["h"]) * 3600 + int(m["m"]) * 60 + float(m["s"])) if m else 0.0
    black = sum(float(x) for x in _BLACK_RE.findall(stderr))
    mv = _MEAN_RE.search(stderr)
    mean_db = None if not mv else (float("-inf") if mv.group(1) == "-inf" else float(mv.group(1)))
    return duration, black, mean_db, bool(_VIDEO_RE.search(stderr)), bool(_AUDIO_RE.search(stderr))


async def quality_check(path: Path, expected_s: float, settings: Settings) -> QCResult:
    """One ffmpeg pass: stream presence, duration, blackdetect share, mean volume."""
    s = settings
    ffmpeg = ffmpeg_exe(s)
    if not ffmpeg:
        raise AssembleError("ffmpeg not found (Settings → Paths)")
    res = await run_cmd([ffmpeg, "-hide_banner", "-nostats", "-nostdin", "-i", str(path),
                         "-map", "0:v:0?", "-map", "0:a:0?",
                         "-vf", f"blackdetect=d={s.clip.qc_black_min_s}",
                         "-af", "volumedetect", "-f", "null", "-"],
                        s.edit.render_timeout_s, grace_s=s.app.shutdown_timeout_s)
    duration, black, mean_db, has_v, has_a = parse_qc(res.err_text)
    black_frac = black / duration if duration > 0 else 1.0

    def result(ok: bool, reason: str) -> QCResult:
        return QCResult(ok, reason, duration, black_frac, mean_db, has_v, has_a)

    if res.returncode != 0:
        return result(False, "unreadable capture")
    if not has_v:
        return result(False, "no video stream")
    if not has_a:
        return result(False, "no audio stream")
    if duration < expected_s * s.clip.qc_min_duration_ratio:
        return result(False, f"too short ({duration:.1f}s of {expected_s:.1f}s)")
    if black_frac >= s.clip.qc_max_black:
        return result(False, f"mostly black ({black_frac:.0%})")
    if mean_db is None or mean_db <= s.clip.qc_min_mean_db:
        return result(False, f"silent (mean {mean_db} dB)")
    return result(True, "ok")

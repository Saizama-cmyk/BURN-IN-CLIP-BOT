"""Detect this PC's capabilities and recommend settings ("auto-tune").

Measures: CPU threads, RAM, NVIDIA GPU + VRAM (nvidia-smi), whether ffmpeg's h264_nvenc
encoder actually works, free space on the buffer drive, and download speed (a short timed
download from a public speed-test endpoint; skipped if it fails). ``recommend`` turns that
into concrete settings, each with a one-line reason. Nothing is applied until you choose to.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass

import httpx

from .config import AppPaths, Settings, ffmpeg_exe
from .util import CmdTimeout, run_cmd

logger = logging.getLogger("clipbot.hardware")

SPEEDTEST_URL = "https://speed.cloudflare.com/__down?bytes={n}"
SPEEDTEST_BYTES = 25_000_000
MIB = 1024 * 1024
GIB = 1024 * MIB

# Budgets used by the recommendations (per stream / per job). These are engineering
# estimates for a 720p60–1080p60 Twitch/Kick capture, not user preferences.
STREAM_RAM_MB = 180            # streamlink (python) + ffmpeg copy per capture
STREAM_MBPS_720 = 4.5          # typical 720p60 bitrate
STREAM_MBPS_1080 = 7.5         # typical 1080p60 bitrate
NET_HEADROOM = 0.6             # use at most 60 % of measured bandwidth for captures
RAM_HEADROOM = 0.35            # captures may use at most 35 % of RAM
THREADS_PER_CAPTURE = 0.5      # captures are light; ~2 per CPU thread is fine
THREADS_PER_CUT = 4            # an x264 raw cut keeps ~4 threads busy
THREADS_PER_RENDER_X264 = 8    # a 1080x1920 x264 render
NVENC_SESSIONS = 3             # stay well under consumer NVENC session limits
WHISPER_VRAM_GB = 2.0          # large-v3-turbo int8_float16 per worker
VISION_8B_VRAM_GB = 12.0       # comfortable VRAM for qwen3-vl:8b next to the judge
MAX_SLOTS = 30
MAX_CUTS = 8


@dataclass
class Hardware:
    cpu_threads: int
    ram_gb: float
    gpu_name: str
    vram_gb: float
    nvenc: bool
    disk_free_gb: float
    net_mbps: float | None
    whisper_device: str

    def to_dict(self) -> dict:
        return asdict(self)


def _ram_gb() -> float:
    if os.name != "nt":
        return 0.0

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullTotalPhys / GIB


async def _gpu(settings: Settings) -> tuple[str, float]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return "", 0.0
    try:
        res = await run_cmd([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                            settings.capture.probe_timeout_s)
        name, mem = [x.strip() for x in res.out_text.splitlines()[0].split(",")]
        return name, float(mem) / 1024
    except (OSError, CmdTimeout, ValueError, IndexError) as exc:
        logger.debug("nvidia-smi: %s", exc)
        return "", 0.0


async def _nvenc_works(settings: Settings) -> bool:
    ffmpeg = ffmpeg_exe(settings)
    if not ffmpeg:
        return False
    try:
        res = await run_cmd([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                             "testsrc2=size=1280x720:rate=30:duration=1", "-c:v", "h264_nvenc",
                             "-f", "null", "-"], settings.capture.probe_timeout_s)
        return res.returncode == 0
    except (OSError, CmdTimeout):
        return False


async def _net_mbps(http: httpx.AsyncClient, timeout_s: float) -> float | None:
    try:
        t0 = time.perf_counter()
        n = 0
        async with http.stream("GET", SPEEDTEST_URL.format(n=SPEEDTEST_BYTES),
                               timeout=timeout_s) as r:
            if r.status_code != 200:
                return None
            async for chunk in r.aiter_bytes():
                n += len(chunk)
        dt = time.perf_counter() - t0
        return round(n * 8 / dt / 1_000_000, 1) if dt > 0 and n else None
    except httpx.HTTPError as exc:
        logger.info("speed test skipped: %s", exc)
        return None


async def detect(settings: Settings, paths: AppPaths, http: httpx.AsyncClient,
                 whisper_status: str, measure_network: bool = True) -> Hardware:
    gpu_name, vram = await _gpu(settings)
    nvenc = await _nvenc_works(settings) if gpu_name else False
    try:
        free = shutil.disk_usage(paths.buffer).free / GIB
    except OSError:
        free = 0.0
    net = await _net_mbps(http, settings.discovery.http_timeout_s * 4) if measure_network else None
    return Hardware(cpu_threads=os.cpu_count() or 1, ram_gb=round(_ram_gb(), 1),
                    gpu_name=gpu_name, vram_gb=round(vram, 1), nvenc=nvenc,
                    disk_free_gb=round(free, 1), net_mbps=net,
                    whisper_device="cpu" if whisper_status.startswith("cpu") else "cuda")


def recommend(hw: Hardware, settings: Settings) -> list[dict]:
    """[{field, value, current, reason}] — only fields whose value would change are marked."""
    out: list[dict] = []

    def rec(field: str, value, reason: str) -> None:
        section, name = field.split(".")
        current = getattr(getattr(settings, section), name)
        out.append({"field": field, "value": value, "current": current,
                    "change": value != current, "reason": reason})

    # --- stream capacity: the tightest of RAM, CPU and network budgets
    hi_res = hw.net_mbps is not None and hw.net_mbps * NET_HEADROOM >= STREAM_MBPS_1080 * MAX_SLOTS / 2
    per_stream = STREAM_MBPS_1080 if hi_res else STREAM_MBPS_720
    by_ram = int(hw.ram_gb * 1024 * RAM_HEADROOM / STREAM_RAM_MB)
    by_cpu = int(hw.cpu_threads / THREADS_PER_CAPTURE)
    by_net = int(hw.net_mbps * NET_HEADROOM / per_stream) if hw.net_mbps else by_cpu
    total = max(2, min(MAX_SLOTS * 2, by_ram, by_cpu, by_net))
    limit = min(("RAM", by_ram), ("CPU", by_cpu), ("network", by_net), key=lambda kv: kv[1])[0]
    both = settings.twitch.enabled and settings.kick.enabled
    per_platform = max(1, min(MAX_SLOTS, total // 2 if both else total))
    why = (f"{hw.cpu_threads} CPU threads, {hw.ram_gb:.0f} GB RAM"
           + (f", {hw.net_mbps:.0f} Mbps down" if hw.net_mbps else "") + f" — {limit} is the limit")
    rec("twitch.slots", per_platform, why)
    rec("kick.slots", per_platform, why)
    rec("capture.quality", "1080p60,1080p,720p60,720p,best" if hi_res else "720p60,720p,best",
        "enough bandwidth for 1080p on every stream" if hi_res
        else "720p keeps every stream smooth on this connection")

    # --- encoding
    if hw.nvenc:
        rec("edit.encoder", "h264_nvenc", f"{hw.gpu_name} can encode on the GPU (frees the CPU)")
        rec("workers.editors", min(NVENC_SESSIONS, max(1, hw.cpu_threads // THREADS_PER_RENDER_X264 + 1)),
            "parallel GPU encodes within the NVENC session limit")
    else:
        rec("edit.encoder", "libx264", "no working NVENC encoder found")
        rec("workers.editors", max(1, hw.cpu_threads // THREADS_PER_RENDER_X264),
            f"one x264 render per {THREADS_PER_RENDER_X264} CPU threads")
        rec("edit.preset", "faster" if hw.cpu_threads >= THREADS_PER_RENDER_X264 * 2 else "veryfast",
            "x264 speed/quality balance for this CPU")
    rec("workers.pipelines", max(1, min(MAX_CUTS, hw.cpu_threads // THREADS_PER_CUT)),
        f"one raw cut per {THREADS_PER_CUT} CPU threads")

    # --- speech + vision on the GPU
    if hw.whisper_device == "cuda" and hw.vram_gb:
        rec("workers.transcribers", max(1, min(4, int(hw.vram_gb // (WHISPER_VRAM_GB * 4)))),
            f"{hw.vram_gb:.0f} GB VRAM, shared with the AI models")
        rec("whisper.device", "cuda", "Whisper runs on the GPU")
    else:
        rec("workers.transcribers", max(1, hw.cpu_threads // THREADS_PER_RENDER_X264),
            "Whisper on CPU (install the CUDA libraries to use the GPU)")
    if settings.ai.vision_enabled:
        big = hw.vram_gb >= VISION_8B_VRAM_GB
        rec("ai.vision_model", "qwen3-vl:8b" if big else "qwen3-vl:4b",
            f"{hw.vram_gb:.0f} GB VRAM " + ("fits the 8B vision model" if big else
                                            "— the 4B vision model leaves room for the judge"))

    # --- keep the machine responsive with many streams
    rec("detector.audio_poll_s", round(max(3.0, total / 4), 1),
        "loudness checks spread out so many captures don't pile up ffmpeg calls")
    rec("backlog.capacity", max(20, total * 3), "room for spikes from every watched stream")
    rec("app.min_free_gb", max(5.0, round(total * 0.25, 1)),
        f"{hw.disk_free_gb:.0f} GB free on the buffer drive")
    return out

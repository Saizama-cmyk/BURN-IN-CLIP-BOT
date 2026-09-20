"""Let the judge "see": grab frames from a raw cut and have a local vision model describe them.

qwen3-coder (the clipability judge) is text-only, so vision is a separate first step:
ffmpeg pulls ``ai.vision_frames`` JPEG frames spread over the clip, a vision model in Ollama
(``ai.vision_model``, e.g. qwen3-vl) describes what happens on screen frame by frame, and that
timestamped description is added to the judge's prompt next to the transcript and chat.

Vision is best-effort: if the model is missing or fails, the clip is still judged on
transcript + chat + signals, and the judge is told that no visual description exists.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import Settings, ffmpeg_exe
from .util import ERR_SNIPPET, CmdTimeout, run_cmd

logger = logging.getLogger("clipbot.vision")


@dataclass
class VisionResult:
    text: str          # timestamped description ("" when unavailable)
    frames: int
    error: str = ""
    times: list[float] | None = None


def frame_times(duration: float, count: int, spike_at: float | None) -> list[float]:
    """``count`` timestamps evenly across the clip; the one nearest the spike snaps onto it
    so the moment chat reacted to is always looked at."""
    if duration <= 0 or count <= 0:
        return []
    times = [round((2 * i + 1) * duration / (2 * count), 2) for i in range(count)]  # midpoints
    if spike_at is not None and 0 <= spike_at <= duration:
        nearest = min(range(count), key=lambda i: abs(times[i] - spike_at))
        times[nearest] = round(spike_at, 2)
    return sorted(set(times))


async def extract_frames(video: Path, times: list[float], settings: Settings) -> list[bytes]:
    """One small JPEG per timestamp (scaled to ``ai.vision_frame_width``)."""
    ffmpeg = ffmpeg_exe(settings)
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found (Settings → Paths)")
    ai = settings.ai

    async def grab(t: float) -> bytes:
        res = await run_cmd([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                             "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                             "-vf", f"scale={ai.vision_frame_width}:-2",
                             "-q:v", str(ai.vision_jpeg_quality), "-f", "image2pipe",
                             "-vcodec", "mjpeg", "-"], settings.capture.probe_timeout_s)
        if res.returncode != 0 or not res.stdout:
            raise RuntimeError(f"frame at {t:.1f}s: {res.err_text.strip()[:ERR_SNIPPET]}")
        return res.stdout

    return list(await asyncio.gather(*(grab(t) for t in times)))


def _save_frames(folder: Path, frames: list[bytes]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for i, data in enumerate(frames):
        (folder / f"{i}.jpg").write_bytes(data)


def vision_prompt(settings: Settings, times: list[float], streamer: str, category: str) -> str:
    stamps = ", ".join(f"frame {i + 1} = {t:.1f}s" for i, t in enumerate(times))
    return (settings.ai.vision_prompt.replace("{streamer}", streamer)
            .replace("{category}", category or "unknown").replace("{frames}", stamps))


class Vision:
    def __init__(self, settings: Settings, http: httpx.AsyncClient,
                 gpu_lock: asyncio.Lock | None = None) -> None:
        self.settings = settings
        self.http = http
        self._lock = gpu_lock or asyncio.Lock()     # shared with the judge: one model at a time
        self.busy = False
        self.last_error = ""
        self._think_supported = True

    def apply(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def base(self) -> str:
        return self.settings.ai.ollama_url.rstrip("/")

    async def _post(self, body: dict) -> httpx.Response:
        """POST /api/chat, retrying transient failures (timeouts, dropped connections, Ollama
        5xx while it swaps models on a shared GPU)."""
        ai = self.settings.ai
        for attempt in range(ai.vision_retries + 1):
            try:
                r = await self.http.post(f"{self.base}/api/chat", json=body, timeout=ai.timeout_s)
                if r.status_code < 500 or attempt == ai.vision_retries:
                    return r
                why = f"Ollama HTTP {r.status_code}"
            except httpx.TransportError as exc:
                if attempt == ai.vision_retries:
                    raise
                why = str(exc) or type(exc).__name__
            logger.info("vision call failed (%s); retry %d/%d in %.0fs", why, attempt + 1,
                        ai.vision_retries, ai.vision_retry_wait_s)
            await asyncio.sleep(ai.vision_retry_wait_s)
        raise RuntimeError("unreachable")

    async def _chat(self, prompt: str, images: list[str]) -> str:
        ai = self.settings.ai
        body = {"model": ai.vision_model, "stream": False, "keep_alive": ai.vision_keep_alive,
                "messages": [{"role": "user", "content": prompt, "images": images}],
                "options": {"temperature": ai.vision_temperature, "num_ctx": ai.vision_num_ctx}}
        if ai.vision_disable_thinking and self._think_supported:
            body["think"] = False
        r = await self._post(body)
        if r.status_code == 400 and "think" in r.text.lower() and "think" in body:
            self._think_supported = False           # model has no thinking switch: retry plain
            body.pop("think")
            r = await self._post(body)
        if r.status_code == 404:
            raise RuntimeError(f"vision model {ai.vision_model!r} is not installed — "
                               f"run: ollama pull {ai.vision_model}")
        if r.status_code != 200:
            raise RuntimeError(f"Ollama HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
        content = str(r.json().get("message", {}).get("content", "")).strip()
        if not content and body.get("think") is False:
            body.pop("think")                    # some models answer only when allowed to think
            r = await self._post(body)
            if r.status_code == 200:
                content = str(r.json().get("message", {}).get("content", "")).strip()
        return content

    async def describe(self, video: Path, duration: float, spike_at: float | None,
                       streamer: str, category: str, save_dir: Path | None = None,
                       frames: int | None = None) -> VisionResult:
        ai = self.settings.ai
        if not ai.vision_enabled:
            return VisionResult("", 0)
        times = frame_times(duration, frames or ai.vision_frames, spike_at)
        try:
            frames = await extract_frames(video, times, self.settings)
            if save_dir is not None:          # keep what the AI saw, for the dashboard
                await asyncio.to_thread(_save_frames, save_dir, frames)
            images = [base64.b64encode(f).decode("ascii") for f in frames]
            saved_times = list(times)          # every saved frame keeps its own timestamp
            async with self._lock:
                self.busy = True
                try:
                    text = await self._chat(vision_prompt(self.settings, times, streamer,
                                                          category), images)
                    if not text and len(images) > 1:
                        # answer cut off (context full): retry with every other frame
                        times, images = times[::2], images[::2]
                        logger.info("vision reply empty; retrying with %d frames", len(images))
                        text = await self._chat(vision_prompt(self.settings, times, streamer,
                                                              category), images)
                finally:
                    self.busy = False
            if not text:
                raise RuntimeError("vision model returned an empty description")
            self.last_error = ""
            return VisionResult(text[:ai.vision_max_chars], len(frames), times=saved_times)
        except (httpx.HTTPError, RuntimeError, CmdTimeout, ValueError) as exc:
            error = str(exc) or type(exc).__name__
            # while Ollama is down every clip fails the same way; say it once, not 200 times
            level = logger.info if error == self.last_error else logger.warning
            level("vision skipped for %s: %s", video.name, error)
            self.last_error = error
            return VisionResult("", 0, self.last_error)


# --------------------------------------------------------------------------- facecam finder
GROUND_SCALE = 1000          # Qwen-VL grounding coordinates run 0..1000


def box_from_grounding(data: dict, src_w: int, src_h: int, min_pct: float,
                       max_pct: float) -> list[int] | None:
    """Convert {"facecam": true, "box": [x1,y1,x2,y2]} (0..1000) into a pixel x,y,w,h box, or
    None if there's no facecam or the box is implausible."""
    if not isinstance(data, dict) or not data.get("facecam"):
        return None
    box = data.get("box")
    try:
        x1, y1, x2, y2 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    x1, x2 = sorted((max(0.0, min(GROUND_SCALE, x1)), max(0.0, min(GROUND_SCALE, x2))))
    y1, y2 = sorted((max(0.0, min(GROUND_SCALE, y1)), max(0.0, min(GROUND_SCALE, y2))))
    area_pct = (x2 - x1) * (y2 - y1) / (GROUND_SCALE * GROUND_SCALE) * 100
    if not (min_pct <= area_pct <= max_pct):
        return None
    left, top = int(x1 / GROUND_SCALE * src_w), int(y1 / GROUND_SCALE * src_h)
    width = int((x2 - x1) / GROUND_SCALE * src_w)
    height = int((y2 - y1) / GROUND_SCALE * src_h)
    width -= width % 2            # even sizes keep ffmpeg's crop/scale happy
    height -= height % 2
    return [left, top, width, height] if width > 0 and height > 0 else None


async def find_facecam(vision: "Vision", frame_jpeg: bytes, src_w: int, src_h: int) -> list[int] | None:
    """Ask the vision model where the webcam is in one frame."""
    s = vision.settings
    body = {"model": s.ai.vision_model, "stream": False, "format": "json",
            "keep_alive": s.ai.vision_keep_alive,
            "messages": [{"role": "user", "content": s.ai.facecam_prompt,
                          "images": [base64.b64encode(frame_jpeg).decode("ascii")]}],
            "options": {"temperature": 0}}
    if s.ai.vision_disable_thinking and vision._think_supported:
        body["think"] = False
    async with vision._lock:
        r = await vision.http.post(f"{vision.base}/api/chat", json=body, timeout=s.ai.timeout_s)
    if r.status_code != 200:
        raise RuntimeError(f"facecam finder: Ollama HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
    try:
        data = json.loads(r.json().get("message", {}).get("content", "") or "{}")
    except ValueError as exc:
        raise RuntimeError(f"facecam finder: bad JSON: {exc}") from exc
    e = s.edit
    return box_from_grounding(data, src_w, src_h, e.facecam_min_area_pct, e.facecam_max_area_pct)

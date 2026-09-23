"""Speech-to-text with one shared faster-whisper model.

The model loads lazily on first use with ``num_workers = workers.transcribers`` so that many
clips can be transcribed concurrently from worker threads. If CUDA fails at load time OR on
the first inference (the usual symptom of missing cuBLAS/cuDNN DLLs), the model is rebuilt
once on CPU with ``whisper.cpu_compute_type`` and the job is retried there.
"""
from __future__ import annotations

import asyncio
import logging
import os
import site
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import AUTO_LANGUAGE, Settings, WhisperCfg
from .models import Word

logger = logging.getLogger("clipbot.transcribe")


@dataclass
class Transcript:
    text: str            # "[start-end] words" lines, fed to the AI
    words: list[Word]
    language: str
    device: str


def _add_cuda_dll_dirs() -> None:
    """Make pip-installed nvidia-cublas/cudnn DLLs visible to CTranslate2 on Windows."""
    if os.name != "nt":
        return
    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    if getattr(sys, "_MEIPASS", None):
        roots.append(sys._MEIPASS)
    for root in roots:
        nvidia = Path(root) / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in nvidia.glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            except OSError as exc:
                logger.debug("add_dll_directory(%s) failed: %s", bin_dir, exc)


def format_transcript(segments: list[tuple[float, float, str]]) -> str:
    return "\n".join(f"[{a:.1f}-{b:.1f}] {t.strip()}" for a, b, t in segments if t.strip())


class Transcriber:
    def __init__(self, settings: Settings, models_dir: Path | None = None) -> None:
        self.settings = settings
        self.models_dir = models_dir
        self._model = None
        self._device = ""
        self._verified = False          # first inference on the current device succeeded
        self._fell_back = False
        self._lock = threading.Lock()
        self._sem = asyncio.Semaphore(settings.workers.transcribers)
        self.status = "not loaded"
        self.jobs = 0

    def apply(self, settings: Settings) -> None:
        old: WhisperCfg = self.settings.whisper
        self.settings = settings
        if (old.model, old.device, old.compute_type, old.cpu_compute_type) != (
                settings.whisper.model, settings.whisper.device, settings.whisper.compute_type,
                settings.whisper.cpu_compute_type):
            logger.info("whisper settings changed; model reloads on next use")
            self.unload()

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._device = ""
            self._verified = False
            self._fell_back = False
            self.status = "not loaded"

    # ------------------------------------------------------------------ loading
    def _build(self, device: str, compute: str):
        from faster_whisper import WhisperModel

        w = self.settings.whisper
        logger.info("loading whisper %s on %s (%s)", w.model, device, compute)
        return WhisperModel(w.model, device=device, compute_type=compute,
                            download_root=str(self.models_dir) if self.models_dir else None,
                            num_workers=self.settings.workers.transcribers)

    def _cpu(self, reason: str) -> None:
        logger.warning("whisper CUDA unavailable (%s); falling back to CPU %s", reason,
                       self.settings.whisper.cpu_compute_type)
        self._model = self._build("cpu", self.settings.whisper.cpu_compute_type)
        self._device = "cpu"
        self._fell_back = True
        self._verified = False
        self.status = f"cpu ({self.settings.whisper.cpu_compute_type}) — CUDA failed: {reason}"

    def _ensure(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            w = self.settings.whisper
            if w.device == "cpu":
                self._model = self._build("cpu", w.cpu_compute_type)
                self._device = "cpu"
                self.status = f"cpu ({w.cpu_compute_type})"
                return
            _add_cuda_dll_dirs()
            try:
                self._model = self._build("cuda", w.compute_type)
                self._device = "cuda"
                self.status = f"cuda ({w.compute_type})"
            except (RuntimeError, OSError, ValueError) as exc:
                self._cpu(str(exc))

    # ------------------------------------------------------------------ inference
    def _run(self, path: str) -> Transcript:
        w = self.settings.whisper
        kwargs = dict(word_timestamps=True, vad_filter=w.vad, beam_size=w.beam_size,
                      language=None if w.language in ("", AUTO_LANGUAGE) else w.language)
        if w.vad:
            kwargs["vad_parameters"] = {"min_silence_duration_ms": w.vad_min_silence_ms}
        model, device = self._model, self._device
        segments, info = model.transcribe(path, **kwargs)
        lines: list[tuple[float, float, str]] = []
        words: list[Word] = []
        for seg in segments:  # the generator does the actual decoding
            lines.append((seg.start, seg.end, seg.text))
            for wd in seg.words or []:
                text = wd.word.strip()
                if text:
                    words.append(Word(start=float(wd.start), end=float(wd.end), text=text))
        return Transcript(format_transcript(lines), words, info.language or "", device)

    def _transcribe_sync(self, path: str) -> Transcript:
        self._ensure()
        try:
            result = self._run(path)
        except IndexError as exc:
            # A cut with no decodable audio - a silent stream, or a file truncated because the
            # drive went away mid-write. The voice detector indexes an empty array and raises.
            # There is nothing to transcribe, which is an answer, not a failure: an empty
            # transcript lets the judge reject the clip instead of the whole task crashing.
            logger.info("no audio in %s (%s); treating it as silence", path, exc)
            return Transcript("", [], "", self._device)
        except (RuntimeError, OSError) as exc:
            with self._lock:
                can_fallback = self._device == "cuda" and not self._verified and not self._fell_back
                if not can_fallback:
                    raise
                self._cpu(str(exc))
            result = self._run(path)
        self._verified = True
        return result

    async def transcribe(self, path: Path) -> Transcript:
        async with self._sem:
            self.jobs += 1
            try:
                return await asyncio.to_thread(self._transcribe_sync, str(path))
            finally:
                self.jobs -= 1

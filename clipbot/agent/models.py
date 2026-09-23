"""The phone's assistant models, mirrored on the PC so the phone downloads them over home Wi-Fi.

A phone pulling a 2.5 GB model straight from the internet is slow and stops whenever the screen
locks. The PC fetches it once, over its own (usually faster, always-on) connection, and the phone
then copies it across the local network, resumably. Only the models the phone app offers are
served - the list below must match LADDER in phone/localAi.js - so this can't be used to make the
PC download arbitrary files.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

logger = logging.getLogger("clipbot.agent.models")

MODELS = {
    "qwen3-4b": ("https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/"
                 "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", 2497281120),
    "llama32-3b": ("https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/"
                   "Llama-3.2-3B-Instruct-Q4_K_M.gguf", 2019377696),
    "qwen3-1.7b": ("https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf",
                   1107409472),
    "gemma3-1b": ("https://huggingface.co/unsloth/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf",
                  806058272),
}
CHUNK = 1 << 20                    # 1 MiB writes
MB = 1 << 20


def models_dir(data_dir: Path) -> Path:
    return data_dir / "agent" / "models"


class ModelMirror:
    """One background download per model; status is polled by the phone."""

    def __init__(self, data_dir: Path, timeout_s: float) -> None:
        self.folder = models_dir(data_dir)
        self.timeout_s = timeout_s
        self._tasks: dict[str, asyncio.Task] = {}
        self._errors: dict[str, str] = {}

    def path(self, model_id: str) -> Path:
        return self.folder / f"{model_id}.gguf"

    def status(self, model_id: str) -> dict:
        if model_id not in MODELS:
            return {"error": "unknown model"}
        size = MODELS[model_id][1]
        done = self.path(model_id)
        part = done.with_suffix(".part")
        have = done.stat().st_size if done.exists() else part.stat().st_size if part.exists() else 0
        running = model_id in self._tasks and not self._tasks[model_id].done()
        return {"ready": done.exists() and have == size, "bytes": size, "have": have,
                "downloading": running, "error": self._errors.get(model_id, "")}

    def fetch(self, model_id: str) -> dict:
        """Start (or keep) the PC's own download of a model; returns the status."""
        if model_id not in MODELS:
            return {"error": "unknown model"}
        st = self.status(model_id)
        if not st["ready"] and not st["downloading"]:
            self._errors.pop(model_id, None)
            self._tasks[model_id] = asyncio.create_task(self._download(model_id),
                                                        name=f"model-{model_id}")
            st["downloading"] = True
        return st

    async def _download(self, model_id: str) -> None:
        url, size = MODELS[model_id]
        self.folder.mkdir(parents=True, exist_ok=True)
        part = self.path(model_id).with_suffix(".part")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout_s) as http:
                last = -1
                while True:
                    have = part.stat().st_size if part.exists() else 0
                    if have >= size:
                        break
                    if have == last:                 # a whole pass added nothing: give up
                        raise RuntimeError(f"stalled at {have} of {size} bytes")
                    last = have
                    headers = {"Range": f"bytes={have}-"} if have else {}
                    async with http.stream("GET", url, headers=headers) as r:
                        if r.status_code not in (httpx.codes.OK, httpx.codes.PARTIAL_CONTENT):
                            raise RuntimeError(f"HTTP {r.status_code}")
                        mode = "ab" if have and r.status_code == httpx.codes.PARTIAL_CONTENT else "wb"
                        with part.open(mode) as fh:
                            async for chunk in r.aiter_bytes(CHUNK):
                                fh.write(chunk)
            if part.stat().st_size != size:
                raise RuntimeError(f"got {part.stat().st_size} of {size} bytes")
            part.replace(self.path(model_id))
            logger.info("phone model %s ready on the PC (%d MB)", model_id, size // MB)
        except (httpx.HTTPError, OSError, RuntimeError) as exc:
            self._errors[model_id] = str(exc)
            logger.warning("phone model %s download failed: %s", model_id, exc)

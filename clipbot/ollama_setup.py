"""First-run setup for the AI: download the Ollama models this PC's judge profile needs.

A new install has Ollama (the installer offers it) but no models. Ashvane checks what the
active profile uses (judge, vision, writer) and pulls whatever is missing, one at a time, in
the background. Progress shows on the Setup page; nothing is pulled when ``ai.auto_pull`` is off.
"""
from __future__ import annotations

import json
import logging

import httpx

from .aiplan import plan
from .config import Settings

logger = logging.getLogger("clipbot.ollama_setup")


def needed_models(settings: Settings, vram_gb: float | None) -> list[str]:
    ai, cw = settings.ai, settings.copywriter
    p = plan(settings, vram_gb)
    want = [p.judge_model]
    if ai.vision_enabled:
        want.append(ai.vision_model if p.profile != "light" else p.judge_model)
    if cw.enabled and cw.model:
        want.append(cw.model)
    return list(dict.fromkeys(m for m in want if m))


def _same(a: str, b: str) -> bool:
    """'qwen3-vl:8b' matches 'qwen3-vl:8b'; a bare name matches its ':latest'."""
    norm = lambda m: m if ":" in m else m + ":latest"   # noqa: E731
    return norm(a) == norm(b)


class ModelSetup:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http
        self.status: dict = {"pulling": "", "done": [], "error": "", "progress": 0.0}

    @property
    def base(self) -> str:
        return self.settings.ai.ollama_url.rstrip("/")

    async def missing(self, vram_gb: float | None) -> list[str]:
        r = await self.http.get(f"{self.base}/api/tags", timeout=self.settings.ai.timeout_s)
        r.raise_for_status()
        have = [m.get("name", "") for m in r.json().get("models", [])]
        return [m for m in needed_models(self.settings, vram_gb) if not any(_same(m, h) for h in have)]

    async def run(self, vram_gb: float | None) -> None:
        """Pull every missing model (streamed so progress is visible). Never raises."""
        if not self.settings.ai.auto_pull:
            return
        try:
            todo = await self.missing(vram_gb)
        except httpx.HTTPError as exc:
            logger.info("model check skipped (Ollama not reachable yet): %s", exc)
            return
        for name in todo:
            self.status.update(pulling=name, progress=0.0, error="")
            logger.info("downloading AI model %s (first-run setup)", name)
            try:
                async with self.http.stream("POST", f"{self.base}/api/pull", json={"name": name},
                                            timeout=self.settings.ai.pull_timeout_s) as r:
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        ev = json.loads(line)
                        if ev.get("error"):
                            raise RuntimeError(ev["error"])
                        if ev.get("total"):
                            self.status["progress"] = round(ev.get("completed", 0) / ev["total"], 3)
                self.status["done"].append(name)
                logger.info("AI model %s ready", name)
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                self.status["error"] = f"{name}: {exc}"
                logger.warning("could not download model %s: %s", name, exc)
        self.status.update(pulling="", progress=0.0)

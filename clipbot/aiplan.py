"""Which AI setup fits this PC: the judge profile.

  strong    big separate judge (AI → Model) with the full rulebook; vision runs beside it.
            Needs a GPU that can hold both models (≥ ai.strong_vram_gb).
  balanced  one multimodal model (AI → Vision model) sees, judges and writes, so Ollama never
            swaps models. Right for ~10-24 GB cards.
  light     a small multimodal model (AI → Light model) with a stricter, simpler rulebook and a
            higher pass mark, because small models pass too much. For small GPUs or CPU only.
  custom    exactly what AI → Model / One model for everything say.

``auto`` picks from the GPU's memory (read with nvidia-smi); before the first reading it assumes
balanced. Pure and unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Settings

GIB_PER_MIB = 1024
FIXED_PROFILES = ("light", "balanced", "strong", "custom")    # anything else = auto


@dataclass(frozen=True)
class AiPlan:
    profile: str
    judge_model: str
    one_model: bool          # judge uses the same model as vision + writer (no GPU swapping)
    extra_rules: str         # appended to the judge's system prompt
    score_bonus: float       # added to the pass mark


def choose_profile(settings: Settings, vram_gb: float | None) -> str:
    ai = settings.ai
    if ai.judge_profile in FIXED_PROFILES:
        return ai.judge_profile
    if vram_gb is None:
        return "balanced"
    if vram_gb >= ai.strong_vram_gb:
        return "strong"
    return "balanced" if vram_gb >= ai.balanced_vram_gb else "light"


def plan(settings: Settings, vram_gb: float | None) -> AiPlan:
    ai = settings.ai
    profile = choose_profile(settings, vram_gb)
    if profile == "strong":
        return AiPlan(profile, ai.model, False, "", 0.0)
    if profile == "light":
        return AiPlan(profile, ai.light_model or ai.vision_model, True, ai.light_rules,
                      ai.light_score_bonus)
    if profile == "balanced":
        return AiPlan(profile, ai.vision_model, True, "", 0.0)
    one = ai.single_model and ai.vision_enabled                          # custom
    return AiPlan(profile, ai.vision_model if one else ai.model, one, "", 0.0)


def vram_gb(gpu: dict | None) -> float | None:
    """Total GPU memory in GB from the pipeline's nvidia-smi reading (MiB)."""
    if not gpu or not gpu.get("mem_total"):
        return None
    return float(gpu["mem_total"]) / GIB_PER_MIB

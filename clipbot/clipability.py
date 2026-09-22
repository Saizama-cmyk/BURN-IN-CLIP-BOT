"""Ask the local model (Ollama) whether a candidate moment is clip-worthy.

One call at a time (the GPU is shared with Whisper). ``format: "json"`` output is validated;
malformed output gets ``ai.retries`` more attempts, then the candidate is rejected as
``model_error``. If Ollama is unreachable, ``AIUnavailable`` is raised so the pipeline holds
the item instead of rejecting it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

from .config import AICfg, ClipCfg, Settings
from .aiplan import AiPlan, plan
from .models import Candidate
from .util import ERR_SNIPPET

logger = logging.getLogger("clipbot.clipability")

PASS_CATEGORY = "moment"
REJECT_CATEGORIES = ("chat_only", "keyword_false", "dead_air")
CATEGORIES = (PASS_CATEGORY, *REJECT_CATEGORIES)
SCORE_MAX = 10.0  # the 0-10 scale the prompt asks for
PEAK_DEFAULT_FRACTION = 1 / 3
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class AIUnavailable(RuntimeError):
    """Ollama cannot be reached (or the model is missing); retry later."""


class BadModelOutput(ValueError):
    pass


def system_prompt(ai: AICfg) -> str:
    text = ai.system_prompt
    for key in ("title_max", "caption_max", "hashtags_min", "hashtags_max"):
        text = text.replace("{" + key + "}", str(getattr(ai, key)))
    return text


def build_user_message(c: Candidate, ai: AICfg) -> str:
    ev, tgt = c.event, c.event.target
    duration = max(0.0, c.end_wall - c.start_wall)
    spike_at = max(0.0, ev.t_wall - c.start_wall)
    transcript = c.transcript.strip() or "(no speech detected)"
    if len(transcript) > ai.transcript_max_chars:
        transcript = transcript[:ai.transcript_max_chars] + "\n…(truncated)"
    chat = "\n".join(f"- {line}" for line in ev.chat_sample) or "(no chat captured)"
    platform = "Twitch" if str(tgt.platform) == "twitch" else "Kick"
    return (
        f"Streamer: {tgt.display_name} ({platform}, {tgt.viewers} viewers)\n"
        f"Category: {tgt.category or 'unknown'}\n"
        f"Stream title: {tgt.title or '(none)'}\n"
        f"Clip length: {duration:.1f}s; detector fired at {spike_at:.1f}s\n"
        f"Signal: {ev.kind} (chat z={ev.chat_z:.2f}, chat {ev.chat_rate:.2f} msg/s vs baseline "
        f"{ev.baseline:.2f}; keyword score={ev.keyword_score:.2f}; audio z={ev.audio_z:.2f})\n"
        f"Keywords hit: {', '.join(ev.keywords_hit) or 'none'}\n\n"
        f"Chat sample:\n{chat}\n\n"
        f"Transcript (seconds from clip start):\n{transcript}\n\n"
        f"What is visible on screen (vision model, seconds from clip start):\n"
        f"{c.visual.strip() or '(no visual description available — judge from the rest)'}\n")


def _extract_json(text: str) -> dict:
    text = _FENCE_RE.sub("", text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise BadModelOutput("no JSON object in reply")
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise BadModelOutput(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BadModelOutput("reply is not a JSON object")
    return data


def clamp_trims(start: float, end: float, duration: float, clip: ClipCfg) -> tuple[float, float]:
    """Keep the AI's trim inside the clip and within [min_len_s, max_len_s]."""
    if duration <= clip.min_len_s:
        return 0.0, duration
    start = min(max(0.0, start), duration)
    end = min(max(start, end), duration)
    if end - start < clip.min_len_s:
        missing = clip.min_len_s - (end - start)
        start = max(0.0, start - missing / 2)
        end = min(duration, start + clip.min_len_s)
        start = max(0.0, end - clip.min_len_s)
    if end - start > clip.max_len_s:
        end = start + clip.max_len_s
    return round(start, 2), round(end, 2)


def ev_peak_default(start: float, end: float) -> float:
    """No peak given: assume the beat sits a third of the way in."""
    return start + (end - start) * PEAK_DEFAULT_FRACTION


def parse_verdict(text: str, duration: float, settings: Settings,
                  min_score: float | None = None) -> dict:
    """Validate and normalise the model's JSON. Raises BadModelOutput."""
    ai, clip = settings.ai, settings.clip
    data = _extract_json(text)
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in ("pass", "reject"):
        raise BadModelOutput(f"verdict must be pass|reject, got {data.get('verdict')!r}")
    category = str(data.get("category", "")).strip().lower()
    if category not in CATEGORIES:
        raise BadModelOutput(f"unknown category {data.get('category')!r}")
    try:
        score = float(data.get("score"))
    except (TypeError, ValueError) as exc:
        raise BadModelOutput(f"score is not a number: {data.get('score')!r}") from exc
    score = min(SCORE_MAX, max(0.0, score))
    title = " ".join(str(data.get("title", "")).split())[:ai.title_max]
    caption = str(data.get("caption", "")).strip()[:ai.caption_max]
    raw_tags = data.get("hashtags", [])
    if isinstance(raw_tags, str):
        raw_tags = raw_tags.replace(",", " ").split()
    if not isinstance(raw_tags, list):
        raise BadModelOutput("hashtags must be a list")
    tags: list[str] = []
    for tag in raw_tags:
        t = re.sub(r"[^\w]", "", str(tag).lstrip("#"))
        if t and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    tags = tags[:ai.hashtags_max]
    try:
        t_start = float(data.get("trim_start", 0.0))
        t_end = float(data.get("trim_end", duration))
    except (TypeError, ValueError) as exc:
        raise BadModelOutput("trim_start/trim_end must be numbers") from exc
    t_start, t_end = clamp_trims(t_start, t_end, duration, clip)
    try:                                     # optional: where the edit punches in
        peak = float(data.get("peak_at", ev_peak_default(t_start, t_end)))
    except (TypeError, ValueError):
        peak = ev_peak_default(t_start, t_end)
    peak = min(max(t_start, peak), t_end)
    passed = verdict == "pass" and score >= (ai.min_score if min_score is None else min_score)
    if passed and not title:
        raise BadModelOutput("passing verdict without a title")
    return {"verdict": verdict, "category": category, "score": round(score, 1),
            "peak_at": round(peak, 2),
            "reason": str(data.get("reason", "")).strip(), "title": title, "caption": caption,
            "hashtags": tags, "trim_start": t_start, "trim_end": t_end, "passed": passed}


class Clipability:
    def __init__(self, settings: Settings, http: httpx.AsyncClient,
                 gpu_lock: asyncio.Lock | None = None) -> None:
        self.settings = settings
        self.http = http
        self._lock = gpu_lock or asyncio.Lock()     # shared with Vision: one model at a time
        self.busy = False
        self.last_error = ""

    def apply(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def base(self) -> str:
        return self.settings.ai.ollama_url.rstrip("/")

    async def list_models(self) -> list[str]:
        try:
            r = await self.http.get(f"{self.base}/api/tags", timeout=self.settings.discovery.http_timeout_s)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIUnavailable(f"Ollama not reachable at {self.base}: {exc}") from exc
        return sorted(m.get("name", "") for m in r.json().get("models", []) if m.get("name"))

    vram_gb: float | None = None          # set by the pipeline from nvidia-smi

    def plan(self) -> AiPlan:
        return plan(self.settings, self.vram_gb)

    def one_model(self) -> bool:
        return self.plan().one_model

    def model(self) -> str:
        return self.plan().judge_model

    async def _chat(self, messages: list[dict]) -> str:
        ai = self.settings.ai
        one = self.one_model()
        body = {"model": self.model(), "messages": messages, "format": "json", "stream": False,
                "keep_alive": ai.keep_alive,
                # same num_ctx as the vision calls: a different context size makes Ollama reload
                "options": {"temperature": ai.temperature, "num_ctx": ai.vision_num_ctx if one else ai.num_ctx}}
        if one:          # qwen3-vl returns an EMPTY reply under format=json; it answers JSON anyway
            body.pop("format")
            if ai.vision_disable_thinking:
                body["think"] = False
        try:
            r = await self.http.post(f"{self.base}/api/chat", json=body, timeout=ai.timeout_s)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise AIUnavailable(f"Ollama not reachable at {self.base}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise BadModelOutput(f"model timed out after {ai.timeout_s}s") from exc
        if r.status_code == 404:
            raise AIUnavailable(f"model {self.model()!r} not found in Ollama: {r.text[:ERR_SNIPPET]}")
        if r.status_code >= 500:
            raise BadModelOutput(f"Ollama HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
        if r.status_code != 200:
            raise AIUnavailable(f"Ollama HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
        try:
            return str(r.json()["message"]["content"])
        except (ValueError, KeyError, TypeError) as exc:
            raise BadModelOutput(f"unexpected Ollama response: {r.text[:ERR_SNIPPET]}") from exc

    async def judge(self, c: Candidate, duration: float | None = None,
                    min_score: float | None = None) -> dict:
        """Return a normalised verdict dict. Raises AIUnavailable when Ollama is down."""
        s = self.settings
        duration = duration if duration is not None else max(0.0, c.end_wall - c.start_wall)
        p = self.plan()
        prompt = system_prompt(s.ai) + (f"\n\n{p.extra_rules}" if p.extra_rules else "")
        if p.score_bonus:
            min_score = (s.ai.min_score if min_score is None else min_score) + p.score_bonus
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": build_user_message(c, s.ai)}]
        async with self._lock:
            self.busy = True
            try:
                last_err = ""
                for attempt in range(s.ai.retries + 1):
                    try:
                        text = await self._chat(messages)
                        verdict = parse_verdict(text, duration, s, min_score)
                        self.last_error = ""
                        return verdict
                    except BadModelOutput as exc:
                        last_err = str(exc)
                        logger.warning("clipability attempt %d for %s: %s", attempt + 1, c.id, exc)
                self.last_error = last_err
                return {"verdict": "reject", "category": "model_error", "score": 0.0,
                        "reason": f"model_error: {last_err}", "title": "", "caption": "",
                        "hashtags": [], "trim_start": 0.0, "trim_end": duration, "passed": False}
            except AIUnavailable as exc:
                self.last_error = str(exc)
                raise
            finally:
                self.busy = False


DEAD_AIR = "dead_air"


def looks_like_dead_air(c: Candidate, ai: AICfg) -> bool:
    """True when nothing happened that the judge could possibly reward.

    Needs all three to be true: almost nothing said, no jump in loudness, and no keyword hit.
    Any one of them is enough to earn a proper look."""
    if not ai.prefilter:
        return False
    words = len((c.transcript or "").split())
    if words >= ai.prefilter_words:
        return False
    if abs(c.event.audio_z) >= ai.prefilter_audio_z:
        return False
    return not c.event.keywords_hit

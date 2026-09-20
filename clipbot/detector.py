"""Hype-moment detector. Pure logic: no I/O, time is always passed in.

Three signals per stream, evaluated every tick:

* chat   — messages/s over ``window_s`` vs a rolling baseline of past window rates over
           ``baseline_s``: z = (rate - mean) / max(std, std_floor). Fires when z ≥ z_threshold
           and rate ≥ min_msgs_per_s. The baseline only uses samples older than the current
           window, so a spike never dilutes its own baseline.
* keyword — weighted keyword hits per second over the window. Must beat keyword_threshold AND
           keyword_baseline_mult × its own baseline (big chats spam KEKW constantly).
           Matching: multi-word keywords are substrings; single words are token matches;
           keywords of ≤ short_keyword_len chars (e.g. "W") only count as the whole message.
* audio  — newest buffer loudness vs the previous ``audio_history`` readings (needs
           ``audio_min_samples``); fires when its z ≥ audio_z_threshold while still fresh.

Nothing fires until ``min_history_s`` of chat history exists. Per-stream cooldown.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field

from .config import DetectorCfg
from .models import SpikeEvent, StreamTarget

logger = logging.getLogger("clipbot.detector")


@dataclass
class _Track:
    first_seen: float
    messages: deque = field(default_factory=deque)          # (t, text) inside the window
    samples: deque = field(default_factory=deque)           # (t, rate, kw_score) per tick
    chat_tail: deque = field(default_factory=deque)         # last N texts for the AI
    spark: deque = field(default_factory=deque)             # (rate, baseline) for the UI
    audio: deque = field(default_factory=deque)             # (t, level_db)
    audio_z: float = 0.0
    audio_z_t: float = -math.inf
    last_fire: float = -math.inf
    last: dict = field(default_factory=dict)


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / len(values)
    return m, math.sqrt(var)


class KeywordMatcher:
    """Weighted keyword scoring for one chat message."""

    def __init__(self, keywords: dict[str, float], short_len: int) -> None:
        self.phrases: list[tuple[str, str, float]] = []   # (original, lowered, weight)
        self.tokens: dict[str, tuple[str, float]] = {}
        self.whole: dict[str, tuple[str, float]] = {}
        for kw, weight in keywords.items():
            k = kw.strip()
            if not k:
                continue
            low = k.lower()
            if len(k) <= short_len:
                self.whole[low] = (k, float(weight))
            elif any(ch.isspace() for ch in k):
                self.phrases.append((k, low, float(weight)))
            else:
                self.tokens[low] = (k, float(weight))

    def score(self, text: str) -> tuple[float, list[str]]:
        low = text.strip().lower()
        if not low:
            return 0.0, []
        total, hits = 0.0, []
        if low in self.whole:
            kw, w = self.whole[low]
            total += w
            hits.append(kw)
        for kw, klow, w in self.phrases:
            if klow in low:
                total += w
                hits.append(kw)
        seen: set[str] = set()
        for tok in low.split():
            if tok in self.tokens and tok not in seen:
                seen.add(tok)
                kw, w = self.tokens[tok]
                total += w
                hits.append(kw)
        return total, hits


class Detector:
    def __init__(self, cfg: DetectorCfg) -> None:
        self.cfg = cfg
        self._tracks: dict[str, _Track] = {}
        self._matcher = KeywordMatcher(cfg.keywords, cfg.short_keyword_len)

    # ---------------------------------------------------------------- config
    def apply(self, cfg: DetectorCfg) -> None:
        self.cfg = cfg
        self._matcher = KeywordMatcher(cfg.keywords, cfg.short_keyword_len)
        for tr in self._tracks.values():
            tr.chat_tail = deque(tr.chat_tail, maxlen=cfg.chat_sample_size or None)
            tr.spark = deque(tr.spark, maxlen=cfg.sparkline_points)
            tr.audio = deque(tr.audio, maxlen=cfg.audio_history + 1)

    def _track(self, key: str, now: float) -> _Track:
        tr = self._tracks.get(key)
        if tr is None:
            tr = _Track(first_seen=now,
                        chat_tail=deque(maxlen=self.cfg.chat_sample_size or None),
                        spark=deque(maxlen=self.cfg.sparkline_points),
                        audio=deque(maxlen=self.cfg.audio_history + 1))
            self._tracks[key] = tr
        return tr

    def track(self, key: str, now: float) -> None:
        """Start the warm-up clock for a stream even before its first message."""
        self._track(key, now)

    def remove(self, key: str) -> None:
        self._tracks.pop(key, None)

    def keys(self) -> list[str]:
        return list(self._tracks)

    # ---------------------------------------------------------------- inputs
    def add_message(self, key: str, text: str, t: float) -> None:
        tr = self._track(key, t)
        tr.messages.append((t, text))
        if self.cfg.chat_sample_size:
            tr.chat_tail.append(text)

    def add_audio(self, key: str, level_db: float, t: float) -> None:
        """Feed one loudness reading (dB, mean volume of the newest buffer segment)."""
        if level_db is None or math.isnan(level_db) or math.isinf(level_db):
            return
        tr = self._track(key, t)
        prior = [lv for _, lv in tr.audio][-self.cfg.audio_history:]
        tr.audio.append((t, level_db))
        if len(prior) >= self.cfg.audio_min_samples:
            m, s = _mean_std(prior)
            tr.audio_z = (level_db - m) / max(s, self.cfg.std_floor)
        else:
            tr.audio_z = 0.0
        tr.audio_z_t = t

    # ---------------------------------------------------------------- evaluate
    def evaluate(self, target: StreamTarget, now: float) -> SpikeEvent | None:
        cfg = self.cfg
        tr = self._track(target.key, now)
        cutoff = now - cfg.window_s
        while tr.messages and tr.messages[0][0] <= cutoff:
            tr.messages.popleft()

        in_window = [txt for t, txt in tr.messages if t <= now]
        rate = len(in_window) / cfg.window_s
        kw_total, hits = 0.0, []
        for txt in in_window:
            s, h = self._matcher.score(txt)
            kw_total += s
            hits.extend(h)
        kw_score = kw_total / cfg.window_s

        # baseline from samples strictly older than the current window
        while tr.samples and tr.samples[0][0] < now - cfg.baseline_s:
            tr.samples.popleft()
        past = [(r, k) for t, r, k in tr.samples if t <= cutoff]
        mean, std = _mean_std([r for r, _ in past])
        kw_mean, _ = _mean_std([k for _, k in past])
        z = (rate - mean) / max(std, cfg.std_floor)
        tr.samples.append((now, rate, kw_score))
        tr.spark.append((round(rate, 3), round(mean, 3)))

        warm = (now - tr.first_seen) >= cfg.min_history_s and bool(past)
        audio_fresh = (now - tr.audio_z_t) <= cfg.window_s
        audio_z = tr.audio_z if audio_fresh else 0.0

        chat_fire = warm and z >= cfg.z_threshold and rate >= cfg.min_msgs_per_s
        kw_fire = (warm and kw_score >= cfg.keyword_threshold
                   and kw_score >= cfg.keyword_baseline_mult * kw_mean)
        audio_fire = audio_fresh and audio_z >= cfg.audio_z_threshold

        tr.last = {"rate": rate, "baseline": mean, "std": std, "z": z, "kw_score": kw_score,
                   "kw_baseline": kw_mean, "audio_z": audio_z, "warm": warm}

        # two weak signals together beat one strong one: this is where most good moments live
        m = cfg.combo_mult
        weak = [warm and z >= cfg.z_threshold * m and rate >= cfg.min_msgs_per_s * m,
                warm and kw_score >= cfg.keyword_threshold * m,
                audio_fresh and audio_z >= cfg.audio_z_threshold * m]
        combo_fire = cfg.combo_enabled and sum(weak) >= cfg.combo_signals

        fired = [name for name, on in (("chat", chat_fire), ("keyword", kw_fire),
                                       ("audio", audio_fire), ("combo", combo_fire)) if on]
        if not fired or now - tr.last_fire < cfg.cooldown_s:
            return None
        tr.last_fire = now
        strong = [f for f in fired if f != "combo"]
        kind = "mixed" if len(strong) > 1 else (strong[0] if strong else "combo")
        score = 0.0
        if chat_fire:
            score += z / cfg.z_threshold
        if kw_fire:
            score += kw_score / cfg.keyword_threshold if cfg.keyword_threshold else kw_score
        if combo_fire and not strong:
            score += (max(0.0, z) / cfg.z_threshold + audio_z / cfg.audio_z_threshold) / 2
        if audio_fire:
            score += audio_z / cfg.audio_z_threshold
        uniq_hits = list(dict.fromkeys(hits))
        logger.info("spike %s kind=%s z=%.2f kw=%.2f audio_z=%.2f", target.key, kind, z,
                    kw_score, audio_z)
        return SpikeEvent(target=target, t_wall=now, kind=kind, score=round(score, 3),
                          chat_rate=round(rate, 3), baseline=round(mean, 3),
                          keywords_hit=uniq_hits, chat_sample=list(tr.chat_tail),
                          chat_z=round(z, 3), keyword_score=round(kw_score, 3),
                          audio_z=round(audio_z, 3))

    # ---------------------------------------------------------------- dashboard
    def recent_chat(self, key: str, n: int) -> list[str]:
        """The last ``n`` chat messages seen for ``key`` (for the live viewer)."""
        tr = self._tracks.get(key)
        return list(tr.chat_tail)[-n:] if tr and n > 0 else []

    def stats(self, key: str) -> dict:
        tr = self._tracks.get(key)
        if tr is None:
            return {"rate": 0.0, "baseline": 0.0, "z": 0.0, "kw_score": 0.0, "audio_z": 0.0,
                    "audio_db": None, "warm": False, "spark": []}
        last = tr.last or {}
        return {"rate": round(last.get("rate", 0.0), 3),
                "baseline": round(last.get("baseline", 0.0), 3),
                "z": round(last.get("z", 0.0), 3),
                "kw_score": round(last.get("kw_score", 0.0), 3),
                "audio_z": round(last.get("audio_z", 0.0), 3),
                "audio_db": tr.audio[-1][1] if tr.audio else None,
                "warm": bool(last.get("warm", False)),
                "spark": [list(p) for p in tr.spark]}

"""Synthesize ClipBot's sound cues from scratch (no samples): metallic chimes and soft hits
that match the chrome look. Pure Python (wave + math), 44.1 kHz mono 16-bit.

    python art/sounds.py
Writes clipbot/dashboard/static/sfx/<cue>.wav.
"""
from __future__ import annotations

import logging
import math
import random
import struct
import wave
from pathlib import Path

logger = logging.getLogger("clipbot.art")
RATE = 44100
OUT = Path(__file__).resolve().parent.parent / "clipbot" / "dashboard" / "static" / "sfx"


def env(t: float, a: float, d: float) -> float:
    """Fast attack, exponential decay."""
    return (t / a) if t < a else math.exp(-(t - a) / d)


def bell(f: float, t: float, decay: float = 0.35) -> float:
    """Metallic partials (inharmonic like struck metal)."""
    parts = ((1.0, 1.0), (2.76, 0.45), (5.4, 0.22), (8.93, 0.1))
    return sum(amp * math.sin(2 * math.pi * f * mult * t) * math.exp(-t * mult / (decay * 3))
               for mult, amp in parts)


def render(notes: list[tuple[float, float, float]], length: float, kind: str = "bell",
           decay: float = 0.35, noise: float = 0.0) -> list[float]:
    """notes: (start_s, freq, gain)."""
    n = int(RATE * length)
    buf = [0.0] * n
    rnd = random.Random(7)
    for start, f, g in notes:
        s0 = int(start * RATE)
        for i in range(s0, n):
            t = (i - s0) / RATE
            e = env(t, 0.004, decay)
            if e < 1e-4 and t > 0.05:
                break
            if kind == "bell":
                v = bell(f, t, decay)
            elif kind == "pluck":
                v = math.sin(2 * math.pi * f * t) + 0.3 * math.sin(4 * math.pi * f * t)
            else:                                   # thud: sine sweep down
                v = math.sin(2 * math.pi * (f * (1 - 0.5 * min(1, t / 0.12))) * t)
            if noise:
                v += noise * (rnd.random() * 2 - 1) * math.exp(-t / 0.02)
            buf[i] += g * e * v
    # tiny room: two early reflections
    out = buf[:]
    for delay, g in ((0.023, 0.25), (0.041, 0.15)):
        d = int(delay * RATE)
        for i in range(d, n):
            out[i] += buf[i - d] * g
    peak = max(1e-9, max(abs(x) for x in out))
    return [x / peak * 0.8 for x in out]


def fade(buf: list[float]) -> list[float]:
    k = int(RATE * 0.01)
    for i in range(k):
        buf[-1 - i] *= i / k
    return buf


def save(name: str, buf: list[float]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, x)) * 32767)) for x in fade(buf)))


C5, E5, G5, B5, C6, D6, E6, G6 = 523.25, 659.25, 783.99, 987.77, 1046.5, 1174.7, 1318.5, 1568.0
CUES = {
    "clip": lambda: render([(0, E5, 1), (0.09, B5, 0.9)], 0.7),                      # bright two-tone
    "post": lambda: render([(0, C5, .8), (0.08, E5, .8), (0.16, G5, .9), (0.26, C6, 1)], 1.1),  # rising
    "reject": lambda: render([(0, 180, 1)], 0.35, kind="thud", decay=0.08, noise=0.15),
    "spike": lambda: render([(0, G6, .7), (0.04, D6, .6), (0.08, G6, .8)], 0.6, decay=0.18),
    "trouble": lambda: render([(0, 220, 1), (0.22, 220, 1)], 0.6, kind="pluck", decay=0.09),
    "hi": lambda: render([(0, G5, 1), (0.07, D6, .8)], 0.55, decay=0.25),
    "sleep": lambda: render([(0, E5, .8), (0.18, C5, .7), (0.38, 392.0, .6)], 1.2, decay=0.4),
    "love": lambda: render([(0, C6, .7), (0.06, E6, .7), (0.12, G6, .6)], 0.8, decay=0.3),
    "click": lambda: render([(0, 2400, 1)], 0.08, kind="pluck", decay=0.012, noise=0.3),
    "open": lambda: render([(0, B5, .6), (0.05, E6, .7)], 0.5, decay=0.2),
}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name, make in CUES.items():
        save(name, make())
        logger.info("sfx %s", name)


if __name__ == "__main__":
    main()

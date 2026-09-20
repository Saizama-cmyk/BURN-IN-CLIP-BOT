"""G10: one real clipability call against the local Ollama model (no mocks)."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from clipbot.clipability import AIUnavailable, Clipability
from clipbot.config import Settings
from clipbot.models import Candidate, Platform, SpikeEvent, StreamTarget

TRANSCRIPT = """[0.0-4.1] okay chat one more game, if we lose this one I'm going to bed
[4.2-9.8] there's one guy left, he's behind the wall, I've got like ten HP
[10.1-13.0] no no no no he's peeking
[13.2-15.9] OH MY GOD ONE TAP, ONE TAP, DID YOU SEE THAT
[16.0-21.5] that's the clutch, that's the clutch of the year, chat clip it right now
[21.6-26.0] I'm literally shaking, look at my hands"""


async def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    s = Settings()
    t = StreamTarget(Platform.TWITCH, "demo", "DemoStreamer", "VALORANT", 18000,
                     "RANKED GRIND to Radiant")
    ev = SpikeEvent(target=t, t_wall=1000.0, kind="mixed", score=3.1, chat_rate=41.0,
                    baseline=9.5, keywords_hit=["clip it", "no way", "OMEGALUL"],
                    chat_sample=["CLIP IT", "NO WAY", "ONE TAP", "OMEGALUL", "HOLY", "clip that"],
                    chat_z=6.3, keyword_score=4.2, audio_z=3.9)
    c = Candidate(id=ev.id, event=ev, raw_path="", start_wall=985.0, end_wall=1012.0,
                  transcript=TRANSCRIPT)
    async with httpx.AsyncClient() as http:
        judge = Clipability(s, http)
        t0 = time.time()
        try:
            verdict = await judge.judge(c, 27.0)
        except AIUnavailable as exc:
            print(f"OLLAMA UNREACHABLE {exc}")
            return 2
    print(f"LIVE VERDICT ({time.time() - t0:.1f}s, model {s.ai.model}) "
          + json.dumps(verdict, ensure_ascii=False))
    return 0 if verdict["category"] != "model_error" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

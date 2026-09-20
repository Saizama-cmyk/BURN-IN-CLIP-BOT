"""End-to-end smoke test on a real live Twitch stream, without API keys.

Discovery needs Twitch keys, so this script injects known channel logins as targets, then
runs the real pipeline: streamlink capture → buffer → (injected) spike → cut → QC → Whisper →
Qwen clipability → vertical render. Pass channel logins as arguments; the first one that is
live and producing segments is used.

    python scripts/e2e_live.py xqc kaicenat jynxzi
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("CLIPBOT_HOME", str(Path(tempfile.gettempdir()) / "clipbot-e2e"))

from clipbot.app import setup_logging
from clipbot.config import load_settings, merge_incoming, resolve_paths, save_settings
from clipbot.models import Platform, SpikeEvent, Stage, StreamTarget
from clipbot.pipeline import Pipeline

TERMINAL = {Stage.SCHEDULED, Stage.REJECTED, Stage.FAILED, Stage.POSTED}


async def main(logins: list[str]) -> int:
    s = merge_incoming(load_settings(), {"capture": {"buffer_s": 120},
                                         "clip": {"pre_s": 20, "post_s": 8}})
    save_settings(s)
    setup_logging(s, console=True)
    log = logging.getLogger("clipbot.e2e")
    p = Pipeline(s, resolve_paths(s))
    await p.start()
    targets = [StreamTarget(Platform.TWITCH, x, x, "unknown", 0) for x in logs(logins)]

    async def fixed_refresh():
        return targets
    p.discovery.refresh = fixed_refresh          # no keys: keep our injected targets
    await p._set_targets(targets)
    try:
        chosen = None
        deadline = time.time() + 90
        while time.time() < deadline and not chosen:
            await asyncio.sleep(5)
            for t in targets:
                cap = p.captures.get(t.key)
                if cap and len(cap.segments()) >= (s.clip.pre_s + s.clip.post_s) / s.capture.segment_s + 1:
                    chosen = t
                    break
        if not chosen:
            log.error("no channel produced a buffer in 90 s: %s",
                      {t.login: p.captures.get(t.key).snapshot() for t in targets})
            return 1
        cap = p.captures.get(chosen.key)
        log.info("using %s: %s; chat %s", chosen.login, cap.snapshot(), p.chat.snapshot()["twitch"])
        segs = cap.segments()
        spike_t = segs[-1].end - s.clip.post_s
        ev = SpikeEvent(target=chosen, t_wall=spike_t, kind="chat", score=1.0,
                        chat_rate=p.detector.stats(chosen.key)["rate"],
                        baseline=p.detector.stats(chosen.key)["baseline"],
                        chat_sample=list(p.detector._tracks[chosen.key].chat_tail)
                        if chosen.key in p.detector._tracks else [], chat_z=3.5)
        cid = await p.submit_event(ev)
        t0 = time.time()
        while time.time() - t0 < 900:
            await asyncio.sleep(3)
            c = await asyncio.to_thread(p.store.get_candidate, cid)
            if c and c.stage in TERMINAL:
                break
        c = await asyncio.to_thread(p.store.get_candidate, cid)
        out = {"channel": chosen.login, "stage": str(c.stage), "error": c.error,
               "seconds": round(time.time() - t0, 1), "whisper": p.transcriber.status,
               "transcript_lines": len(c.transcript.splitlines()),
               "visual": c.visual, "vision_error": p.vision.last_error,
               "verdict": c.verdict, "final_path": c.final_path}
        print("E2E RESULT " + json.dumps(out, ensure_ascii=False))
        return 0 if c.stage in (Stage.SCHEDULED, Stage.REJECTED) and c.verdict else 1
    finally:
        await p.stop()


def logs(xs: list[str]) -> list[str]:
    return [x.strip().lower() for x in xs if x.strip()]


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:] or ["xqc", "kaicenat", "jynxzi", "caseoh_"])))

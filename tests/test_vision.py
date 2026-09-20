import asyncio
import base64
import json

import httpx

from clipbot.clipability import build_user_message
from clipbot.config import Settings, merge_incoming
from clipbot.models import Candidate, Platform, SpikeEvent, StreamTarget
from clipbot.vision import Vision, extract_frames, frame_times, vision_prompt


def test_frame_times_even_and_snap_to_spike():
    assert frame_times(12.0, 4, None) == [1.5, 4.5, 7.5, 10.5]
    assert frame_times(12.0, 4, 8.2) == [1.5, 4.5, 8.2, 10.5]      # nearest frame → spike
    assert frame_times(0, 4, None) == []


def test_extract_frames_real(lavfi_clip):
    s = merge_incoming(Settings(), {"ai": {"vision_frame_width": 320}})
    frames = asyncio.run(extract_frames(lavfi_clip, [1.0, 4.0], s))
    assert len(frames) == 2 and all(f[:2] == b"\xff\xd8" for f in frames)   # JPEG magic


def test_vision_prompt_placeholders():
    txt = vision_prompt(Settings(), [1.5, 8.2], "xQc", "GTA V")
    assert "xQc" in txt and "GTA V" in txt and "frame 2 = 8.2s" in txt and "{" not in txt


def run_vision(lavfi_clip, handler, **ai):
    s = merge_incoming(Settings(), {"ai": {"vision_frames": 3, "vision_frame_width": 256, **ai}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await Vision(s, http).describe(lavfi_clip, 8.0, 5.0, "Streamer", "Valorant")
    return asyncio.run(go())


def test_describe_sends_images_to_vision_model(lavfi_clip):
    seen = {}

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen.update(body)
        return httpx.Response(200, json={"message": {"content": "5.0s: headshot.\nOverall: kill at 5s"}})

    res = run_vision(lavfi_clip, handler)
    msg = seen["messages"][0]
    assert seen["model"] == Settings().ai.vision_model and seen["think"] is False
    assert len(msg["images"]) == 3
    assert base64.b64decode(msg["images"][0])[:2] == b"\xff\xd8"
    assert res.text.startswith("5.0s: headshot") and res.frames == 3 and not res.error


def test_missing_model_is_graceful(lavfi_clip):
    res = run_vision(lavfi_clip, lambda req: httpx.Response(404, json={"error": "model not found"}))
    assert res.text == "" and "ollama pull" in res.error


def test_model_without_thinking_switch_retried(lavfi_clip):
    calls = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        calls.append("think" in body)
        if "think" in body:
            return httpx.Response(400, json={"error": "model does not support thinking"})
        return httpx.Response(200, json={"message": {"content": "Overall: nothing notable"}})

    res = run_vision(lavfi_clip, handler)
    assert calls == [True, False] and res.text == "Overall: nothing notable"


def test_vision_off_does_nothing(lavfi_clip):
    res = run_vision(lavfi_clip, lambda req: (_ for _ in ()).throw(AssertionError("no call")),
                     vision_enabled=False)
    assert res.text == "" and res.frames == 0


def test_judge_prompt_includes_visual():
    t = StreamTarget(Platform.KICK, "s", "S", "Slots", 1)
    ev = SpikeEvent(target=t, t_wall=10.0, kind="chat", score=1, chat_rate=1, baseline=1)
    c = Candidate(id=ev.id, event=ev, raw_path="", start_wall=0, end_wall=20,
                  visual="12.0s: jackpot animation\nOverall: big win at 12s")
    msg = build_user_message(c, Settings().ai)
    assert "What is visible on screen" in msg and "jackpot animation" in msg
    c.visual = ""
    assert "no visual description available" in build_user_message(c, Settings().ai)


def test_vision_retries_transient_ollama_failures():
    import asyncio as _a
    import httpx as _h
    from clipbot.config import Settings as _S, merge_incoming as _m
    from clipbot.vision import Vision
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            raise _h.ReadTimeout("slow load")
        if len(calls) == 2:
            return _h.Response(500, text="runner crashed")
        return _h.Response(200, json={"message": {"content": "a streamer screams"}})

    s = _m(_S(), {"ai": {"vision_retries": 2, "vision_retry_wait_s": 0}})

    async def go():
        async with _h.AsyncClient(transport=_h.MockTransport(handler)) as http:
            return await Vision(s, http)._chat("p", [])
    assert _a.run(go()) == "a streamer screams" and len(calls) == 3

import asyncio
import json

import httpx
import pytest

from clipbot.clipability import AIUnavailable, Clipability, clamp_trims, parse_verdict
from clipbot.config import ClipCfg, Settings
from clipbot.models import Candidate, Platform, SpikeEvent, StreamTarget

GOOD = {"verdict": "pass", "category": "moment", "score": 8, "reason": "big reaction",
        "title": "He could NOT believe it", "caption": "chat lost it",
        "hashtags": ["#gaming", "clutch", "Gaming", "twitch clips"], "trim_start": 3.0,
        "trim_end": 30.0}


def cand() -> Candidate:
    t = StreamTarget(Platform.TWITCH, "streamer", "Streamer", "Valorant", 5000, "ranked")
    ev = SpikeEvent(target=t, t_wall=1000.0, kind="chat", score=2.0, chat_rate=12, baseline=3,
                    keywords_hit=["KEKW"], chat_sample=["KEKW", "no way"], chat_z=4.2)
    return Candidate(id=ev.id, event=ev, raw_path="x.mp4", start_wall=975.0, end_wall=1012.0,
                     transcript="[0.0-3.0] okay watch this\n[20.0-24.0] NO WAY")


def client(replies: list, calls: list) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        reply = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(200, json={"message": {"role": "assistant", "content": reply}})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def judge(replies, settings=None):
    calls: list = []

    async def go():
        async with client(replies, calls) as http:
            return await Clipability(settings or Settings(), http).judge(cand(), 37.0)
    return asyncio.run(go()), calls


def test_valid_json_is_parsed():
    v, calls = judge([json.dumps(GOOD)])
    assert len(calls) == 1
    assert v["passed"] is True and v["score"] == 8.0 and v["category"] == "moment"
    assert v["hashtags"] == ["gaming", "clutch", "twitchclips"]
    body = calls[0]
    # default is one-model mode: the vision model judges (no JSON mode, same context as vision)
    assert body["model"] == Settings().ai.vision_model and "format" not in body
    assert body["options"]["num_ctx"] == Settings().ai.vision_num_ctx
    assert body["options"]["temperature"] == Settings().ai.temperature
    assert body["keep_alive"] == Settings().ai.keep_alive
    assert "Streamer" in body["messages"][1]["content"] and "NO WAY" in body["messages"][1]["content"]


def test_malformed_twice_rejects_after_one_retry():
    v, calls = judge(["not json at all", "{broken"])
    assert len(calls) == 2
    assert v["passed"] is False and v["category"] == "model_error"


def test_malformed_then_valid_recovers():
    v, calls = judge(["```json\n{oops", "```json\n" + json.dumps(GOOD) + "\n```"])
    assert len(calls) == 2 and v["passed"] is True


def test_schema_violation_counts_as_bad_output():
    bad = dict(GOOD, verdict="maybe")
    v, calls = judge([json.dumps(bad), json.dumps(bad)])
    assert len(calls) == 2 and v["category"] == "model_error"


def test_low_score_does_not_pass():
    v, _ = judge([json.dumps(dict(GOOD, score=3))])
    assert v["verdict"] == "pass" and v["passed"] is False


def test_ollama_down_raises():
    with pytest.raises(AIUnavailable):
        judge([httpx.ConnectError("refused")])


def test_trim_clamping():
    clip = ClipCfg(min_len_s=12, max_len_s=59)
    assert clamp_trims(-5, 100, 37, clip) == (0.0, 37.0)
    s, e = clamp_trims(20, 22, 37, clip)
    assert e - s == pytest.approx(12) and 0 <= s and e <= 37
    assert clamp_trims(0, 90, 120, clip) == (0.0, 59.0)
    assert clamp_trims(0, 5, 8, clip) == (0.0, 8)


def test_parse_limits_lengths():
    s = Settings()
    v = parse_verdict(json.dumps(dict(GOOD, title="x" * 200, caption="y" * 900,
                                      hashtags=[f"t{i}" for i in range(20)])), 37, s)
    assert len(v["title"]) == s.ai.title_max
    assert len(v["caption"]) == s.ai.caption_max
    assert len(v["hashtags"]) == s.ai.hashtags_max


def test_two_model_mode_uses_judge_model_with_json():
    from clipbot.config import merge_incoming
    s = merge_incoming(Settings(), {"ai": {"judge_profile": "custom", "single_model": False}})
    v, calls = judge([json.dumps(GOOD)], s)
    assert v["passed"] and calls[0]["model"] == s.ai.model and calls[0]["format"] == "json"

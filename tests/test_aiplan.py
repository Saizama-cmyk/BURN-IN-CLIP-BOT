from clipbot.aiplan import choose_profile, plan, vram_gb
from clipbot.config import Settings, merge_incoming


def test_auto_profile_follows_gpu_memory():
    s = Settings()
    assert choose_profile(s, None) == "balanced"
    assert choose_profile(s, 32) == "strong" and choose_profile(s, 16) == "balanced"
    assert choose_profile(s, 6) == "light"
    assert vram_gb({"mem_total": 16384.0}) == 16.0 and vram_gb(None) is None


def test_plans():
    s = Settings()
    strong, bal, light = plan(s, 48), plan(s, 16), plan(s, 4)
    assert strong.judge_model == s.ai.model and not strong.one_model
    assert bal.judge_model == s.ai.vision_model and bal.one_model and not bal.extra_rules
    assert light.judge_model == s.ai.light_model and light.one_model
    assert light.score_bonus == s.ai.light_score_bonus and "strict" in light.extra_rules.lower()
    custom = plan(merge_incoming(s, {"ai": {"judge_profile": "custom", "single_model": False}}), 16)
    assert custom.judge_model == s.ai.model and not custom.one_model


def test_light_profile_is_stricter_in_the_judge():
    import asyncio
    import json
    import httpx
    from clipbot.clipability import Clipability
    from tests.test_clipability import GOOD, cand
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": json.dumps(dict(GOOD, score=6.6))}})

    s = merge_incoming(Settings(), {"ai": {"judge_profile": "light", "min_score": 6.0}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await Clipability(s, http).judge(cand(), 37.0)
    v = asyncio.run(go())
    assert not v["passed"]                                 # 6.6 < 6.0 + 1.0 light bonus
    assert seen[0]["model"] == s.ai.light_model and "strict" in seen[0]["messages"][0]["content"].lower()

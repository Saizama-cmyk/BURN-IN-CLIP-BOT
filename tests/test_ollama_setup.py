import asyncio
import json

import httpx

from clipbot.config import Settings, merge_incoming
from clipbot.ollama_setup import ModelSetup, needed_models


def test_needed_models_follow_profile():
    s = Settings()
    assert needed_models(s, 16) == [s.ai.vision_model]                       # balanced: one model
    assert needed_models(s, 48) == [s.ai.model, s.ai.vision_model]           # strong: judge + vision
    assert needed_models(s, 4) == [s.ai.light_model]                         # light


def test_pulls_only_missing_models():
    s = merge_incoming(Settings(), {"ai": {"judge_profile": "strong"}})
    pulled = []

    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": s.ai.model}]})
        pulled.append(json.loads(req.content)["name"])
        body = "\n".join(json.dumps(x) for x in ({"status": "pulling", "total": 10, "completed": 5},
                                                 {"status": "success"}))
        return httpx.Response(200, text=body)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            ms = ModelSetup(s, http)
            await ms.run(48)
            return ms.status
    status = asyncio.run(go())
    assert pulled == [s.ai.vision_model] and status["done"] == [s.ai.vision_model] and not status["error"]

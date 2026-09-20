import asyncio
import time

import httpx
import pytest

from clipbot.config import Settings, merge_incoming
from clipbot.publishers.base import PostText, PublishError, TokenStore
from clipbot.publishers.instagram import InstagramPublisher
from clipbot.publishers.oauth import OAuthManager
from clipbot.publishers.tiktok import chunk_plan


def ig_settings(**posting):
    return merge_incoming(Settings(), {"accounts": {"instagram_token": "typed"},
                                       "posting": {"instagram": posting}})


def ig_mock(calls):
    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, dict(req.url.params)))
        p = req.url.path
        if p.endswith("/refresh_access_token"):
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 5184000})
        if p.endswith("/me"):
            return httpx.Response(200, json={"user_id": "1789", "username": "me"})
        if p.endswith("/1789/media"):
            return httpx.Response(200, json={"id": "C1"})
        if req.url.host == "rupload.facebook.com":
            assert req.headers["Authorization"].startswith("OAuth ")
            return httpx.Response(200, json={"success": True})
        if p.endswith("/C1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if p.endswith("/1789/media_publish"):
            return httpx.Response(200, json={"id": "M9"})
        if p.endswith("/M9"):
            return httpx.Response(200, json={"permalink": "https://instagram.com/reel/x"})
        return httpx.Response(404)
    return handler


def run_ig(settings, tokens, calls, video):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(ig_mock(calls))) as http:
            return await InstagramPublisher(settings, http, tokens).publish(
                video, PostText("t", "c", ["x"]))
    return asyncio.run(go())


def test_instagram_looks_up_id_and_publishes(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 64)
    calls: list = []
    res = run_ig(ig_settings(), TokenStore(tmp_path / "t.json"), calls, video)
    assert res.ok and res.remote_id == "M9" and res.url.endswith("/reel/x")
    assert any(p.endswith("/me") for _, p, _ in calls)
    assert not any(p.endswith("/refresh_access_token") for _, p, _ in calls)
    assert all("v26.0" in p for _, p, _ in calls if "graph" in p or "rupload" in p or "/me" in p)


def test_instagram_refreshes_old_token_and_new_paste_wins(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 64)
    tokens = TokenStore(tmp_path / "t.json")
    calls: list = []
    run_ig(ig_settings(), tokens, calls, video)                  # stores the typed token
    stored = tokens.get("instagram")
    tokens.set("instagram", dict(stored, refreshed_at=time.time() - 31 * 86400))
    calls.clear()
    run_ig(ig_settings(), tokens, calls, video)
    assert any(p.endswith("/refresh_access_token") for _, p, _ in calls)
    assert tokens.get("instagram")["access_token"] == "fresh"
    # the user pastes a brand-new token in Settings: it replaces the stored one
    new = merge_incoming(Settings(), {"accounts": {"instagram_token": "pasted"}})
    calls.clear()
    run_ig(new, tokens, calls, video)
    assert tokens.get("instagram")["access_token"] == "pasted"


def test_tiktok_chunk_plan():
    mb = 1024 * 1024
    assert chunk_plan(3 * mb, 10 * mb) == (3 * mb, 1)
    assert chunk_plan(25 * mb, 10 * mb) == (10 * mb, 2)      # last chunk absorbs 5 MB
    assert chunk_plan(25 * mb, 2 * mb) == (5 * mb, 5)        # clamped to the 5 MB minimum


def test_oauth_paste_url(tmp_path):
    s = merge_incoming(Settings(), {"accounts": {
        "tiktok_client_key": "ck", "tiktok_client_secret": "cs",
        "tiktok_redirect_uri": "https://me.github.io/clipbot/callback/"}})
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = req.content.decode()
        return httpx.Response(200, json={"access_token": "A", "refresh_token": "R",
                                         "expires_in": 86400, "open_id": "o"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            mgr = OAuthManager(s, http, TokenStore(tmp_path / "t.json"))
            url = mgr.start_url("tiktok")
            assert "redirect_uri=https%3A%2F%2Fme.github.io%2Fclipbot%2Fcallback%2F" in url
            state = url.split("state=")[1].split("&")[0]
            with pytest.raises(PublishError):
                await mgr.callback_from_url("tiktok", "https://me.github.io/clipbot/callback/")
            await mgr.callback_from_url(
                "tiktok", f"https://me.github.io/clipbot/callback/?code=XYZ&scopes=a&state={state}")
            assert mgr.connected("tiktok")
            assert "code=XYZ" in seen["body"] and "client_key=ck" in seen["body"]
            with pytest.raises(PublishError):                 # a state can only be used once
                await mgr.callback_from_url("tiktok", f"https://x/?code=XYZ&state={state}")
    asyncio.run(go())

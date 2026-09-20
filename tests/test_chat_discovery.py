import pytest
import asyncio
import json

import httpx

from clipbot.chat import irc_command, parse_kick_event, parse_privmsg
from clipbot.config import PlatformCfg, Settings, merge_incoming
from clipbot.discovery import Discovery, select_targets
from clipbot.models import Platform, StreamTarget


def test_parse_privmsg():
    line = "@badge-info=;color=#FF0000 :user!user@user.tmi.twitch.tv PRIVMSG #XQC :KEKW no way"
    assert parse_privmsg(line) == ("xqc", "KEKW no way")
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome") is None
    assert parse_privmsg(":u!u@u PRIVMSG #chan :\x01ACTION waves\x01") == ("chan", "waves")
    assert irc_command(":tmi.twitch.tv RECONNECT") == "RECONNECT"
    assert irc_command("PING :tmi.twitch.tv") == "PING"


def test_parse_kick_event():
    raw = json.dumps({"event": "App\\Events\\ChatMessageEvent", "channel": "chatrooms.668.v2",
                      "data": json.dumps({"content": "W stream", "sender": {"username": "a"}})})
    ev, ch, data = parse_kick_event(raw)
    assert ev == "App\\Events\\ChatMessageEvent" and ch == "chatrooms.668.v2"
    assert data["content"] == "W stream"


def T(login, viewers, platform=Platform.TWITCH):
    return StreamTarget(platform, login, login, "cat", viewers)


def test_select_targets_order_block_cap():
    cfg = PlatformCfg(slots=4, blocked_streamers=["bad"], min_viewers=100)
    forced = [T("forcedsmall", 5)]
    fcat = [[T("c1", 500), T("c2", 400), T("c3", 300)]]
    top = [[T("bad", 9000), T("t1", 8000), T("t2", 50), T("c1", 500)], [T("t3", 7000)]]
    out = select_targets(cfg, forced, fcat, top, per_category=2)
    assert [t.login for t in out] == ["forcedsmall", "c1", "c2", "t1"]
    assert out[0].forced and not out[1].forced


def twitch_mock(fail_streams=False):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        if req.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert req.headers["Client-Id"] == "cid" and req.headers["Authorization"] == "Bearer tok"
        if req.url.path.endswith("/games/top"):
            return httpx.Response(200, json={"data": [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]})
        if req.url.path.endswith("/streams"):
            if fail_streams:
                return httpx.Response(500, text="boom")
            gid = req.url.params.get("game_id")
            return httpx.Response(200, json={"data": [
                {"user_login": f"s{gid}{i}", "user_name": f"S{gid}{i}", "game_name": gid,
                 "viewer_count": 1000 - i, "title": "t"} for i in range(5)]})
        return httpx.Response(404)
    return handler, calls


def test_twitch_discovery_and_error_keeps_list():
    s = merge_incoming(Settings(), {"twitch": {"client_id": "cid", "client_secret": "sec", "fill_slots": False},
                                    "kick": {"enabled": False},
                                    "discovery": {"top_categories": 2, "streamers_per_category": 2}})

    async def go():
        handler, calls = twitch_mock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            d = Discovery(s, http)
            first = await d.refresh()
            assert [t.login for t in first] == ["s10", "s11", "s20", "s21"]
            assert "4 streams" in d.messages["twitch"]
            # now the API fails: the previous list is kept and the message says so
            bad, _ = twitch_mock(fail_streams=True)
            d.http = httpx.AsyncClient(transport=httpx.MockTransport(bad))
            again = await d.refresh()
            await d.http.aclose()
            assert [t.login for t in again] == ["s10", "s11", "s20", "s21"]
            assert "keeping previous list" in d.messages["twitch"]
    asyncio.run(go())


def test_missing_credentials_graceful():
    async def go():
        async with httpx.AsyncClient() as http:
            d = Discovery(Settings(), http)
            assert await d.refresh() == []
            assert "no client ID/secret" in d.messages["twitch"]
            assert "no client ID/secret" in d.messages["kick"]
    asyncio.run(go())


def test_kick_tolerant_parsing():
    s = merge_incoming(Settings(), {"kick": {"client_id": "k", "client_secret": "s", "languages": []},
                                    "twitch": {"enabled": False},
                                    "discovery": {"top_categories": 1, "streamers_per_category": 2}})

    def handler(req: httpx.Request):
        if req.url.host == "id.kick.com":
            return httpx.Response(200, json={"access_token": "kt", "expires_in": 3600})
        if req.url.path.endswith("/livestreams"):
            return httpx.Response(200, json={"data": [
                {"slug": "a", "viewer_count": 900, "stream_title": "x", "category": {"id": 5, "name": "Slots"}},
                {"channel": {"slug": "b"}, "viewers": 800, "category": {"id": 5, "name": "Slots"}},
                {"slug": "c", "viewer_count": 10, "category": {"id": 9, "name": "IRL"}},
                {"nothing": True}]})
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            d = Discovery(s, http)
            got = await d.refresh()
            assert [(t.login, t.viewers, t.category) for t in got] == [("a", 900, "Slots"),
                                                                       ("b", 800, "Slots")]
    asyncio.run(go())


def test_kick_v2_fallback_pages_sorts_and_chunks():
    """v1 search gone (404) → v2: pages via next_cursor, sends language_code, sorts by viewers.
    Forced streamers are looked up 50 slugs per call; categories use v2 ?name=."""
    forced = [f"f{i}" for i in range(60)]
    s = merge_incoming(Settings(), {"kick": {"client_id": "k", "client_secret": "s",
                                             "languages": ["en"], "forced_streamers": forced,
                                             "forced_categories": ["Slots"]},
                                    "twitch": {"enabled": False},
                                    "discovery": {"top_categories": 1, "streamers_per_category": 3,
                                                  "kick_page_size": 2, "kick_max_pages": 5}})
    seen = {"v1": 0, "v2": [], "channels": [], "cats": []}
    pages = {"": ({"channel": {"slug": "old"}, "viewer_count": 5, "category": {"id": 1}}, "c2"),
             "c2": ({"channel": {"slug": "big"}, "viewer_count": 900, "category": {"id": 1},
                     "broadcaster_user": {"username": "Big"}, "language_code": "en"}, "c3"),
             "c3": ({"channel": {"slug": "mid"}, "viewer_count": 50, "category": {"id": 1}}, "")}

    def handler(req: httpx.Request):
        p = req.url.path
        if req.url.host == "id.kick.com":
            return httpx.Response(200, json={"access_token": "kt", "expires_in": 3600})
        if p.endswith("/v1/livestreams"):
            seen["v1"] += 1
            return httpx.Response(404, json={"message": "gone"})
        if p.endswith("/v2/livestreams"):
            seen["v2"].append(dict(req.url.params.multi_items()))
            item, nxt = pages[req.url.params.get("cursor", "")]
            return httpx.Response(200, json={"data": [item],
                                             "pagination": {"next_cursor": nxt}})
        if p.endswith("/v1/channels"):
            seen["channels"].append(len(req.url.params.get_list("slug")))
            return httpx.Response(200, json={"data": []})
        if p.endswith("/v2/categories"):
            seen["cats"].append(req.url.params.get("name"))
            return httpx.Response(200, json={"data": [{"id": 1, "name": "Slots"}]})
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            d = Discovery(s, http)
            got = await d.refresh()
            assert [t.login for t in got] == ["big", "mid", "old"]      # sorted by viewers
            assert got[0].display_name == "Big"
            assert seen["v1"] == 1                                       # tried once, then v2 only
            assert seen["channels"] == [50, 10]
            assert seen["cats"] == ["Slots"]
            assert all(q.get("language_code") == "en" and q["limit"] == "2" for q in seen["v2"])
            await d.refresh()
            assert seen["v1"] == 1
    asyncio.run(go())


def test_twitch_anonymous_login_is_always_justinfan():
    """A custom nick is refused by Twitch ("Improperly formatted auth") and dropped every ~10 s."""
    import asyncio as _a
    from clipbot.chat import TwitchChat
    from clipbot.config import Settings as _S

    sent = []

    class WS:
        async def send(self, m):
            sent.append(m)

    chat = TwitchChat(_S(), lambda *a: None)
    _a.run(chat.handshake(WS()))
    assert sent[0] == "PASS SCHMOOPIIE" and sent[1].startswith("NICK justinfan")
    assert sent[1][len("NICK justinfan"):].isdigit()
    with pytest.raises(ConnectionRefusedError):
        _a.run(chat.handle(WS(), ":tmi.twitch.tv NOTICE * :Improperly formatted auth\r\n"))
    _a.run(chat.handle(WS(), ":tmi.twitch.tv NOTICE #xqc :This room is in emote-only mode.\r\n"))


def test_fill_slots_when_min_viewers_is_too_strict():
    from clipbot.config import PlatformCfg
    from clipbot.discovery import select_targets
    from clipbot.models import Platform, StreamTarget
    group = [StreamTarget(Platform.TWITCH, f"s{i}", f"S{i}", "Cat", v) for i, v in
             enumerate([50000, 20000, 9000, 5000, 3000, 800])]
    strict = PlatformCfg(slots=4, min_viewers=15000, fill_slots=False)
    assert [t.login for t in select_targets(strict, [], [], [group], 10)] == ["s0", "s1"]
    filled = PlatformCfg(slots=4, min_viewers=15000, fill_slots=True)
    assert [t.login for t in select_targets(filled, [], [], [group], 10)] == ["s0", "s1", "s2", "s3"]

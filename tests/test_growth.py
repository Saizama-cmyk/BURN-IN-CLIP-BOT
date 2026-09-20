"""Copywriter, analytics steering and the popular-clip importer."""
import asyncio
import json
import time

import httpx

from clipbot.analytics import Analytics, compute_insights
from clipbot.clip_import import candidate_from_clip, pick_clips
from clipbot.config import Settings, merge_incoming, resolve_paths
from clipbot.copywriter import Copywriter, normalize_copy
from clipbot.db import Store
from clipbot.models import Candidate, Platform, SpikeEvent, Stage, StreamTarget
from clipbot.scheduler import compose_post


def cand(login="xqc", category="GTA V", kind="chat", post_copy=None, source=None):
    t = StreamTarget(Platform.TWITCH, login, login.title(), category, 1)
    ev = SpikeEvent(target=t, t_wall=1.0, kind=kind, score=1, chat_rate=1, baseline=1)
    return Candidate(id=ev.id, event=ev, raw_path="", start_wall=0, end_wall=20,
                     stage=Stage.SCHEDULED, verdict={"title": "T", "caption": "C", "hashtags": ["a"],
                                                     "score": 7},
                     post_copy=post_copy or {}, source=source or {})


COPY = {"hook": "He did NOT expect that", "youtube": {"title": "xQc loses it", "description": "Story.",
        "tags": ["#xqc", "gta", "GTA", "clips"]},
        "tiktok": {"caption": "wait for it", "hashtags": ["fyp", "#xqc", "gta"]},
        "instagram": {"caption": "Line one.\n\nFollow for daily clips", "hashtags": ["xqc"]},
        "facebook": {"title": "xQc GTA moment", "description": "Setup and payoff."},
        "discord": {"message": "new clip just dropped"}}


def test_normalize_copy_limits():
    out = normalize_copy(COPY, merge_incoming(Settings(), {"copywriter": {"hashtags_max": 2}}))
    assert out["youtube"]["tags"] == ["xqc", "gta", "clips"]           # deduped, # stripped
    assert out["tiktok"]["hashtags"] == ["fyp", "xqc"]                  # capped
    assert out["hook"].startswith("He did")


def test_copywriter_calls_model_with_rules():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": json.dumps(COPY)}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await Copywriter(Settings(), http).write(cand(), "Streamers doing best: xqc")
    out = asyncio.run(go())
    system, user = seen["messages"][0]["content"], seen["messages"][1]["content"]
    assert "Youtube:" in system and "{platform_rules}" not in system
    assert "Streamers doing best: xqc" in user
    assert out["discord"]["message"] == "new clip just dropped"


def test_compose_uses_platform_copy():
    s = Settings()
    c = cand(post_copy=normalize_copy(COPY, s))
    yt = compose_post(c, s, "youtube")
    assert yt.title == "xQc loses it" and yt.caption.startswith("Story.") and "gta" in yt.hashtags
    tt = compose_post(c, s, "tiktok")
    assert tt.title == "wait for it" and "#fyp" in tt.caption
    plain = compose_post(cand(), s, "youtube")
    assert plain.title == "T"                                            # falls back to verdict


def test_import_credit_names_clipper():
    c = cand(source={"type": "twitch_clip", "clipper": "viewer42"})
    assert "clip by viewer42" in compose_post(c, Settings(), "youtube").caption


def _seed(store, n=6):
    now = time.time()
    for i in range(n):
        login = "winner" if i % 2 == 0 else "loser"
        c = cand(login=login, category="Slots" if login == "winner" else "IRL")
        c.created_at = now - 3 * 86400
        store.upsert_candidate(c)
        store.record_post(c.id, "youtube", f"v{i}", "", "ok", when=now - 2 * 86400)
    for p in store.measurable_posts(0):
        views = 10000 if p["streamer"] == "winner" else 500
        store.record_metrics(p["post_id"], views, 10, 1, 0, 0, when=p["posted_at"] + 25 * 3600)


def test_insights_and_steering(tmp_path):
    s = merge_incoming(Settings(), {"analytics": {"min_posts": 4, "steer_strength": 1,
                                                  "explore_share": 0}})
    store = Store(tmp_path / "db")
    _seed(store)
    posts = store.measurable_posts(0)
    ins = compute_insights(posts, store.metrics_for([p["post_id"] for p in posts]), s)
    assert ins.measured == 6
    streamers = ins.groups["streamer"]
    assert streamers["twitch:winner"]["score"] > 0 > streamers["twitch:loser"]["score"]
    an = Analytics(s, store, {}, resolve_paths(s))
    an.insights = ins
    assert an.active
    assert an.bias(cand(login="winner", category="Slots")) > an.bias(cand(login="loser", category="IRL"))
    assert an.boosted_streamers("twitch") == ["winner"]
    assert "winner" in an.notes()
    store.close()


def test_flood_caps_and_exploration(tmp_path):
    s = merge_incoming(Settings(), {"analytics": {"max_share_per_streamer": 0.5,
                                                  "max_share_per_category": 1, "explore_share": 0.5}})
    an = Analytics(s, Store(tmp_path / "db"), {}, resolve_paths(s))
    today = [{"streamer": "xqc", "category": "GTA V"}, {"streamer": "xqc", "category": "GTA V"}]
    assert not an.within_caps(cand("xqc"), today)                       # 3 of 3 would be xqc
    assert an.within_caps(cand("other"), today)
    assert an.within_caps(cand("xqc"), [])                              # first post always ok
    picks = [an.explore_now("youtube") for _ in range(10)]
    assert sum(picks) == 5                                               # exactly half explore


def test_due_logic(tmp_path):
    s = Settings()
    an = Analytics(s, Store(tmp_path / "db"), {}, resolve_paths(s))
    now = time.time()
    assert an.due(now, [], {}) == "periodic"                            # never ran
    an.last_run = now - 2 * 3600
    old_post = {"post_id": 1, "platform": "youtube", "posted_at": now - 30 * 3600}
    assert an.due(now, [old_post], {}) == "next-day"
    assert an.due(now, [dict(old_post, platform="discord")], {}) in ("", "end-of-day")
    an.last_run = now - 60                                              # min gap blocks
    assert an.due(now, [old_post], {}) == ""


def test_pick_clips_filters():
    s = merge_incoming(Settings(), {"clip_import": {"min_views": 1000, "max_per_run": 2},
                                    "twitch": {"languages": ["en"]}})
    clips = [{"id": "a", "view_count": 5000, "duration": 30, "language": "en"},
             {"id": "b", "view_count": 900, "duration": 30, "language": "en"},    # too few views
             {"id": "c", "view_count": 9000, "duration": 90, "language": "en"},   # too long
             {"id": "d", "view_count": 8000, "duration": 20, "language": "es"},   # language
             {"id": "e", "view_count": 7000, "duration": 20, "language": "en"},
             {"id": "f", "view_count": 6000, "duration": 20, "language": "en"}]
    got = pick_clips(clips, s, lambda cid: cid == "e")                   # e already imported
    assert [c["id"] for c in got] == ["f", "a"]


def test_candidate_from_clip(tmp_path):
    c = candidate_from_clip({"id": "Slug-1", "broadcaster_name": "xQc", "broadcaster_id": "71092938",
                             "game_name": "GTA V", "view_count": 12345, "creator_name": "viewer42",
                             "title": "LOL", "duration": 26.5, "url": "https://clips.twitch.tv/x"},
                            tmp_path / "clip.mp4")
    assert c.event.kind == "popular_clip" and c.event.target.login == "xqc"
    assert c.source["clipper"] == "viewer42" and abs((c.end_wall - c.start_wall) - 26.5) < 0.01
    assert "12,345 views" in c.event.chat_sample[0]


def test_copy_keeps_first_comments():
    from clipbot.config import Settings as _S
    from clipbot.copywriter import normalize_copy
    out = normalize_copy({"youtube": {"title": "T", "description": "D", "tags": ["a"], "comment": "who wins this?\nline2"},
                          "tiktok": {"caption": "C", "comment": "rate it 1-10"}, "discord": {"message": "m", "comment": "x"}}, _S())
    assert out["youtube"]["comment"] == "who wins this? line2" and out["tiktok"]["comment"] == "rate it 1-10"
    assert "comment" not in out["discord"]

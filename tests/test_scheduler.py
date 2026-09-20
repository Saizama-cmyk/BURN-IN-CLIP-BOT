import asyncio
import time
from datetime import datetime
from pathlib import Path

from clipbot.config import Settings, merge_incoming
from clipbot.db import Store
from clipbot.models import Candidate, Platform, SpikeEvent, Stage, StreamTarget
from clipbot.publishers.base import PostText, Publisher, PublishResult
from clipbot.scheduler import (Scheduler, compose_post, in_posting_window, pick_candidate,
                               render_template)


def cand(tmp_path: Path, score=7.0, created=None, stage=Stage.SCHEDULED) -> Candidate:
    t = StreamTarget(Platform.TWITCH, "xqc", "xQc", "Just Chatting", 50000)
    ev = SpikeEvent(target=t, t_wall=1.0, kind="chat", score=1, chat_rate=1, baseline=1)
    f = tmp_path / f"{ev.id}.mp4"
    f.write_bytes(b"\x00" * 16)
    c = Candidate(id=ev.id, event=ev, raw_path="", start_wall=0, end_wall=1, stage=stage,
                  final_path=str(f), verdict={"score": score, "title": "Big W", "caption": "wow",
                                              "hashtags": ["xqc", "clips"]})
    if created is not None:
        c.created_at = created
    return c


class FakePub(Publisher):
    name = "discord"

    def __init__(self, settings, ok=True):
        super().__init__(settings, None, None)
        self.ok = ok
        self.calls: list[PostText] = []

    def configured(self):
        return True, ""

    async def _publish(self, video, post):
        self.calls.append(post)
        return PublishResult(self.ok, remote_id="r1", url="https://x", error="" if self.ok else "nope")

    async def delete(self, remote_id):
        return PublishResult(True, remote_id=remote_id)


class OffPub(FakePub):
    def configured(self):
        return False, "not configured"


def pubs(settings, discord):
    out = {n: OffPub(settings) for n in ("youtube", "tiktok", "instagram", "facebook")}
    out["discord"] = discord
    return out


def enabled(**discord):
    return merge_incoming(Settings(), {"posting": {"discord": {"enabled": True, **discord}}})


def test_templates():
    assert render_template("{title} by {streamer} {nope} {", {"title": "T", "streamer": "S"}) == \
        "T by S {nope} {"


def test_compose_post(tmp_path):
    s = merge_incoming(Settings(), {"posting": {"title_template": "{title} | {brand}"},
                                    "brand": {"name": "ChatSpiked", "tiktok_handle": "@cs"}})
    p = compose_post(cand(tmp_path), s, "tiktok")
    assert p.title == "Big W | ChatSpiked"
    assert "🎥 xQc on Twitch" in p.caption and "#xqc #clips" in p.caption
    s2 = merge_incoming(s, {"posting": {"credit_line": False, "caption_template": "{caption} {handle}"}})
    p2 = compose_post(cand(tmp_path), s2, "tiktok")
    assert p2.caption == "wow @cs"


def test_posting_window():
    s = merge_incoming(Settings(), {"posting": {"hours_start": 22, "hours_end": 6}})
    assert in_posting_window(s, datetime(2026, 1, 1, 23))
    assert in_posting_window(s, datetime(2026, 1, 1, 3))
    assert not in_posting_window(s, datetime(2026, 1, 1, 12))
    assert in_posting_window(Settings(), datetime(2026, 1, 1, 12))


def test_pick_prefers_score_after_wait(tmp_path):
    now = time.time()
    old_low = cand(tmp_path, score=6, created=now - 7200)
    old_high = cand(tmp_path, score=9, created=now - 4000)
    fresh = cand(tmp_path, score=10, created=now - 10)
    assert pick_candidate([fresh, old_low, old_high], now, 3600) is old_high
    assert pick_candidate([fresh], now, 3600) is fresh
    assert pick_candidate([cand(tmp_path, created=now - 20), cand(tmp_path, created=now - 30)],
                          now, 3600).created_at == now - 30


def run_ticks(sched: Scheduler, n=1):
    async def go():
        for _ in range(n):
            await sched.tick()
            await asyncio.gather(*sched._busy.values())
    asyncio.run(go())


def test_post_marks_posted_and_respects_gap(tmp_path):
    s = enabled(min_gap_min=60)
    store = Store(tmp_path / "db")
    a, b = cand(tmp_path, created=time.time() - 5), cand(tmp_path, created=time.time() - 1)
    store.upsert_candidate(a)
    store.upsert_candidate(b)
    fake = FakePub(s)
    sched = Scheduler(s, store, pubs(s, fake))
    run_ticks(sched, 3)
    assert len(fake.calls) == 1                         # min gap blocks the second post
    assert store.get_candidate(a.id).stage == Stage.POSTED
    assert store.get_candidate(b.id).stage == Stage.SCHEDULED
    store.close()


def test_daily_cap(tmp_path):
    s = enabled(min_gap_min=0, daily_cap=2)
    store = Store(tmp_path / "db")
    for _ in range(4):
        store.upsert_candidate(cand(tmp_path))
    fake = FakePub(s)
    sched = Scheduler(s, store, pubs(s, fake))
    run_ticks(sched, 5)
    assert len(fake.calls) == 2
    store.close()


def test_failures_retry_then_give_up(tmp_path):
    s = merge_incoming(enabled(min_gap_min=0), {"posting": {"max_attempts": 2}})
    store = Store(tmp_path / "db")
    c = cand(tmp_path)
    store.upsert_candidate(c)
    fake = FakePub(s, ok=False)
    sched = Scheduler(s, store, pubs(s, fake))
    run_ticks(sched, 4)
    assert len(fake.calls) == 2
    got = store.get_candidate(c.id)
    assert got.posted_to["discord"] == "failed" and got.stage == Stage.POSTED
    assert len(store.posts_for_candidate(c.id)) == 2
    store.close()


def test_cleanup_expires_and_deletes_remote(tmp_path):
    s = merge_incoming(enabled(), {"posting": {"retention_days": 7, "delete_remote_on_expiry": True}})
    store = Store(tmp_path / "db")
    old = cand(tmp_path, created=time.time() - 8 * 86400, stage=Stage.POSTED)
    store.upsert_candidate(old)
    store.record_post(old.id, "discord", "m1", "", "ok")
    sched = Scheduler(s, store, pubs(s, FakePub(s)))
    assert asyncio.run(sched.cleanup()) == 1
    assert store.get_candidate(old.id) is None
    assert not Path(old.final_path).exists()
    store.close()


def test_publish_now_ignores_caps_and_window(tmp_path):
    s = merge_incoming(enabled(daily_cap=0), {"posting": {"hours_start": 3, "hours_end": 4}})
    store = Store(tmp_path / "db")
    c = cand(tmp_path)
    store.upsert_candidate(c)
    fake = FakePub(s)
    sched = Scheduler(s, store, pubs(s, fake))
    run_ticks(sched, 2)
    assert fake.calls == []                               # cap 0 + closed window: nothing
    out = asyncio.run(sched.publish_now(c.id, ["discord", "youtube"]))
    assert out["discord"]["ok"] and len(fake.calls) == 1
    assert not out["youtube"]["ok"] and "not configured" in out["youtube"]["error"]
    assert store.get_candidate(c.id).posted_to["discord"] == "ok"
    store.close()

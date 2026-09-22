import os
import time

from clipbot.db import Store
from clipbot.models import Candidate, Platform, SpikeEvent, Stage, StreamTarget, Word


def make(cid_suffix="", stage=Stage.QUEUED, created=None) -> Candidate:
    t = StreamTarget(Platform.KICK, "slug", "Slug", "IRL", 10)
    ev = SpikeEvent(target=t, t_wall=1.0, kind="mixed", score=1.0, chat_rate=1, baseline=1,
                    chat_sample=["a", "b"])
    c = Candidate(id=ev.id, event=ev, raw_path="r.mp4", start_wall=0, end_wall=5, stage=stage,
                  words=[Word(0, 1, "hi")], verdict={"score": 7})
    if created is not None:
        c.created_at = created
    return c


def test_candidate_roundtrip(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    c = make()
    st.upsert_candidate(c)
    got = st.get_candidate(c.id)
    assert got == c
    c.stage = Stage.SCHEDULED
    st.upsert_candidate(c)
    assert [x.id for x in st.candidates_in_stage(Stage.SCHEDULED)] == [c.id]
    assert st.stage_counts(0) == {"scheduled": 1}
    recent = st.recent_candidates(10)
    assert "words" not in recent[0] and "chat_sample" not in recent[0]["event"]
    st.close()


def test_active_and_expiry(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    old = make(created=time.time() - 10 * 86400)
    new = make(stage=Stage.TRANSCRIBE)
    st.upsert_candidate(old)
    st.upsert_candidate(new)
    assert [c.id for c in st.active_candidates()] == [old.id, new.id]
    assert [c.id for c in st.expired_candidates(time.time() - 7 * 86400)] == [old.id]
    st.delete_candidate(old.id)
    assert st.get_candidate(old.id) is None
    st.close()


def test_posts(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    c = make()
    st.upsert_candidate(c)
    st.record_post(c.id, "discord", "1", "u", "ok")
    st.record_post(c.id, "discord", "", "", "failed", "boom")
    assert st.posts_since("discord", 0) == 1                   # only successes count to caps
    assert st.last_post_time("discord") is not None             # attempts count to gaps
    rows = st.posts_for_candidate(c.id)
    assert len(rows) == 2
    st.mark_post_deleted(rows[0]["id"])
    assert st.posts_for_candidate(c.id)[0]["deleted"] == 1
    assert st.recent_posts(5)[0]["login"] == "slug"
    st.close()


def test_candidate_histogram_covers_full_history_and_window_edges(tmp_path):
    from clipbot.pets import GRAPH_HOURS, HOUR_S, PASSED

    st = Store(tmp_path / "db.sqlite")
    now = 100_000.0
    for i in range(130):  # more than the dashboard's default 100 visible samples
        st.upsert_candidate(make(stage=Stage.SCHEDULED, created=now - i))
    st.upsert_candidate(make(stage=Stage.REJECTED, created=now - HOUR_S))
    st.upsert_candidate(make(stage=Stage.POSTED, created=now - GRAPH_HOURS * HOUR_S + 1))
    st.upsert_candidate(make(stage=Stage.POSTED, created=now - GRAPH_HOURS * HOUR_S))
    st.upsert_candidate(make(stage=Stage.SCHEDULED, created=now + 1))
    history = st.candidate_histogram(now, GRAPH_HOURS, HOUR_S, PASSED)
    assert len(st.recent_candidates(100)) == 100
    assert history["hours"][-1] == 130
    assert history["passed_hours"][-1] == 130
    assert history["hours"][-2] == 1 and history["passed_hours"][-2] == 0
    assert history["hours"][0] == 1 and history["passed_hours"][0] == 1
    assert sum(history["hours"]) == 132  # excludes future and exactly-expired rows
    st.close()


def test_sweeper_clears_scratch_but_never_finished_clips(tmp_path):
    """Work files and dead buffers go; the clips themselves are the product and stay."""
    from clipbot import sweeper
    from clipbot.config import AppPaths

    work, buffer, clips = tmp_path / "work", tmp_path / "buffer", tmp_path / "clips"
    for d in (work, buffer, clips):
        d.mkdir()
    old = work / "abc123_raw.mp4"
    old.write_bytes(b"x" * 1024)
    os.utime(old, (0, 0))                       # older than any keep window
    fresh = work / "new_raw.mp4"
    fresh.write_bytes(b"y" * 1024)
    dead = buffer / "twitch_gone"
    dead.mkdir()
    seg = dead / "seg_20260101000000.ts"
    seg.write_bytes(b"z" * 2048)
    os.utime(seg, (0, 0))
    live = buffer / "twitch_live"
    live.mkdir()
    (live / "seg_20260101000001.ts").write_bytes(b"z" * 2048)
    keeper = clips / "streamer_abc.mp4"
    keeper.write_bytes(b"clip")

    paths = AppPaths(data=tmp_path, buffer=buffer, work=work, clips=clips, logs=tmp_path,
                     db=tmp_path / "db", tokens=tmp_path / "t.json", models=None)
    from clipbot.config import Settings
    settings = Settings()
    settings.app.work_keep_h = 1
    sweeper.sweep(settings, paths, live_keys={"twitch_live"})

    assert not old.exists(), "stale work file should be gone"
    assert fresh.exists(), "work from the last hour is still in use"
    assert not dead.exists(), "buffer for an unwatched stream should be gone"
    assert live.exists(), "the stream being watched keeps its buffer"
    assert keeper.exists(), "finished clips are never swept"

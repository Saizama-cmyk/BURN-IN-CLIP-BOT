"""A bug in one clip must not wedge the queue.

Every stage used to be wrapped in a list of expected exceptions. Anything outside that list - a
NameError from a missing import, an IndexError from a library - escaped the task. The candidate
then kept its stage in the database, still counted as in flight, and was resumed on the next
start, forever. Enough of those and the backlog failsafe latches on and capture stops, which is
how this app ended up hours behind live with dozens of clips "waiting".
"""
import asyncio
import types

import pytest

from clipbot.config import Settings, resolve_paths
from clipbot.models import Candidate, Platform, SpikeEvent, Stage, StreamTarget
from clipbot.pipeline import Pipeline


@pytest.fixture
def pipeline(tmp_path):
    s = Settings()
    s.app.data_dir = str(tmp_path / "data")
    p = Pipeline(s, resolve_paths(s))
    yield p
    p.store.close()


def _candidate() -> Candidate:
    target = StreamTarget(Platform.KICK, "slug", "Slug", "IRL", 10)
    event = SpikeEvent(target=target, t_wall=1.0, kind="chat", score=1.0, chat_rate=1, baseline=1)
    return Candidate(id=event.id, event=event, raw_path="", start_wall=0, end_wall=5)


@pytest.mark.parametrize("boom", [NameError("name 'os' is not defined"),
                                  IndexError("boolean index did not match"),
                                  KeyError("workspace")])
def test_an_unexpected_bug_fails_the_clip_and_frees_the_slot(pipeline, boom):
    c = _candidate()

    async def explode(*_a, **_kw):
        raise boom

    pipeline.captures = types.SimpleNamespace(get=lambda _key: object())   # still capturing
    import clipbot.pipeline as mod
    original = mod.assemble
    mod.assemble = explode
    try:
        asyncio.run(pipeline._run_candidate(c))
    finally:
        mod.assemble = original

    assert c.stage is Stage.FAILED                     # written off, not left mid-stage
    assert type(boom).__name__ in c.error              # and the bug is named, not hidden
    assert pipeline.store.get_candidate(c.id).stage is Stage.FAILED
    assert c.id not in pipeline._inflight              # so it is not resumed on the next start


def test_cancellation_is_not_swallowed(pipeline):
    """Shutdown must still stop a clip, not mark it failed."""
    c = _candidate()

    async def cancelled(*_a, **_kw):
        raise asyncio.CancelledError

    pipeline.captures = types.SimpleNamespace(get=lambda _key: object())
    import clipbot.pipeline as mod
    original = mod.assemble
    mod.assemble = cancelled
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(pipeline._run_candidate(c))
    finally:
        mod.assemble = original


def test_restart_does_not_resume_clips_past_their_moment(pipeline):
    """73 hours-old clips resumed at once flooded the queue and starved capture on start."""
    import time
    from pathlib import Path
    old, fresh = _candidate(), _candidate()
    fresh.event = SpikeEvent(target=old.event.target, t_wall=2.0, kind="chat", score=1.0,
                             chat_rate=1, baseline=1)
    fresh.id = fresh.event.id
    old.created_at = time.time() - 6 * 3600
    for c in (old, fresh):
        c.stage = Stage.QC
        c.raw_path = str(Path(pipeline.paths.work) / f"{c.id}.mp4")
        Path(c.raw_path).parent.mkdir(parents=True, exist_ok=True)
        Path(c.raw_path).write_bytes(b"x")
        pipeline.store.upsert_candidate(c)
    launched = []
    pipeline._launch = lambda c: launched.append(c.id)
    asyncio.run(pipeline._recover())
    assert launched == [fresh.id]
    assert pipeline.store.get_candidate(old.id).stage is Stage.FAILED

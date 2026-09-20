import pytest

from clipbot.config import BacklogCfg, Settings, resolve_paths
from clipbot.pipeline import Pipeline


@pytest.fixture
def pipeline():
    s = Settings()
    p = Pipeline(s, resolve_paths(s))
    yield p
    p.store.close()


def test_failsafe_hysteresis(pipeline):
    assert pipeline._update_failsafe(0.79) is False
    assert pipeline._update_failsafe(0.8) is True       # pause at 0.8
    assert pipeline._update_failsafe(0.6) is True       # still paused at 0.6
    assert pipeline._update_failsafe(0.51) is True
    assert pipeline._update_failsafe(0.5) is False      # resume at 0.5
    assert pipeline._update_failsafe(0.7) is False      # no re-trigger below pause_at
    assert pipeline.paused is False


def test_failsafe_uses_settings(pipeline):
    pipeline.settings = pipeline.settings.model_copy(
        update={"backlog": BacklogCfg(capacity=10, pause_at=0.5, resume_at=0.2)})
    assert pipeline._update_failsafe(0.5) is True
    assert pipeline._update_failsafe(0.3) is True
    assert pipeline._update_failsafe(0.2) is False


def test_resume_must_be_below_pause():
    with pytest.raises(ValueError):
        BacklogCfg(pause_at=0.5, resume_at=0.5)


def test_manual_pause_flag(pipeline):
    pipeline.manual_paused = True
    assert pipeline.paused is True

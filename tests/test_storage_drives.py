"""Clips must land on a drive that is actually there.

The drives listed on the Storage page are a pool. One of them being unplugged, full or simply
gone is normal - a USB stick gets pulled - and must never stop a clip being written, or take the
whole pipeline down with it.
"""
import clipbot.storage as storage
from clipbot.config import Settings, resolve_paths
from clipbot.storage import clip_drives, clips_target


def _paths(tmp_path):
    s = Settings()
    s.app.data_dir = str(tmp_path / "data")
    p = resolve_paths(s)
    p.ensure()
    return s, p


def test_overflow_dirs_are_expanded_and_listed_after_the_main_one(tmp_path, monkeypatch):
    s, p = _paths(tmp_path)
    monkeypatch.setenv("BURNIN_TEST_DRIVE", str(tmp_path / "stick"))
    s.app.overflow_dirs = ["  ", "%BURNIN_TEST_DRIVE%", str(tmp_path / "spare")]

    drives = clip_drives(s, p)
    assert drives[0] == p.clips                       # the main folder is always tried first
    assert drives[1] == tmp_path / "stick"            # %VARS% expanded, blank entries dropped
    assert drives[2] == tmp_path / "spare"


def test_a_full_main_drive_falls_through_to_the_next_one(tmp_path, monkeypatch):
    s, p = _paths(tmp_path)
    spare = tmp_path / "spare"
    s.app.overflow_dirs = [str(spare)]
    s.app.overflow_free_gb = 10.0
    monkeypatch.setattr(storage, "free_gb", lambda path: 1.0 if path == p.clips else 500.0)

    assert clips_target(s, p) == spare


def test_an_unplugged_drive_is_skipped_not_used(tmp_path, monkeypatch):
    """The exact case that broke this app: the main clips folder was on a USB stick."""
    s, p = _paths(tmp_path)
    internal = tmp_path / "internal"
    s.app.overflow_dirs = [str(internal)]
    s.app.overflow_free_gb = 10.0
    # 0 GB is what free_gb reports for a folder it cannot reach at all
    monkeypatch.setattr(storage, "free_gb", lambda path: 0.0 if path == p.clips else 500.0)

    assert clips_target(s, p) == internal


def test_every_drive_tight_uses_the_roomiest_reachable_one(tmp_path, monkeypatch):
    s, p = _paths(tmp_path)
    small, big = tmp_path / "small", tmp_path / "big"
    s.app.overflow_dirs = [str(small), str(big)]
    s.app.overflow_free_gb = 100.0
    sizes = {p.clips: 1.0, small: 2.0, big: 9.0}
    monkeypatch.setattr(storage, "free_gb", lambda path: sizes[path])

    assert clips_target(s, p) == big


def test_nothing_reachable_still_returns_a_path(tmp_path, monkeypatch):
    """The caller gets one clear write error, never a missing value."""
    s, p = _paths(tmp_path)
    s.app.overflow_dirs = [str(tmp_path / "gone")]
    monkeypatch.setattr(storage, "free_gb", lambda path: 0.0)

    assert clips_target(s, p) == p.clips


def test_free_gb_reports_zero_for_a_drive_that_is_not_there(tmp_path):
    missing = tmp_path / "nope.txt"
    missing.write_text("a file, so mkdir underneath it cannot work", encoding="utf-8")
    assert storage.free_gb(missing / "clips") == 0.0

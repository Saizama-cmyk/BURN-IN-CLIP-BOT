"""Shared fixtures. Every test runs against a throwaway CLIPBOT_HOME so the user's real
settings, database and Run key are never touched."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CLIPBOT_HOME", str(home))
    return home


@pytest.fixture(scope="session")
def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        pytest.skip("ffmpeg not on PATH")
    return exe


@pytest.fixture(scope="session")
def lavfi_clip(tmp_path_factory, ffmpeg) -> Path:
    """An 8 s 1280x720 test pattern with a 440 Hz tone."""
    out = tmp_path_factory.mktemp("media") / "src.mp4"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=8", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=8", "-shortest", "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", str(out)], check=True)
    return out

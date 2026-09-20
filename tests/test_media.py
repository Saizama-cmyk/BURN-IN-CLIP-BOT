import asyncio
import shutil
import subprocess

import pytest

from clipbot.config import Settings
from clipbot.media import grab_jpeg

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")


def test_poster_past_the_end_falls_back_to_first_frame(tmp_path):
    video = tmp_path / "short.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x568:rate=30:duration=2", "-pix_fmt", "yuv420p", str(video)],
                   check=True)
    out = asyncio.run(grab_jpeg(video, tmp_path / "p.jpg", 10.0, 180, Settings()))
    assert out is not None and out.stat().st_size > 0
    assert not (tmp_path / "p.tmp.jpg").exists()

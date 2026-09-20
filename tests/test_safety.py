import asyncio
import re
import shutil
import subprocess

import pytest

from clipbot.config import Settings, merge_incoming
from clipbot.models import Word
from clipbot.safety import Filter, _words


def strong():
    return merge_incoming(Settings(), {"safety": {"level": "strong"}})


def test_masks_words_leetspeak_and_stretching():
    f = Filter(strong())
    assert f.mask("what the fuck") == "what the f***"
    assert f.mask("F U C K") == "F U C K"                       # separate letters are not a word
    assert f.mask("fuuuuck yes") == "f****** yes"
    assert f.mask("sh1t happens") == "s*** happens"
    assert f.mask("the fucking best") == "the f****** best"      # stem catches longer forms
    assert f.mask("scunthorpe classic") == "scunthorpe classic"  # whole words only


def test_slur_level_leaves_mild_swearing_but_catches_slurs():
    f = Filter(Settings())                                       # default level = slurs
    slur = sorted(_words("slurs"))[0]
    assert f.mask("damn that was good") == "damn that was good"
    assert f.mask(f"he said {slur}") == f"he said {slur[0]}{'*' * (len(slur) - 1)}"
    assert f.slurs_in(f"he said {slur.upper()} lol") == [slur.upper()]
    assert f.slurs_in("clean sentence") == []


def test_hard_r_blocked_casual_form_allowed():
    import base64
    f = Filter(Settings())
    hard, casual = (base64.b64decode(b).decode() for b in ("bmlnZ2Vy", "bmlnZ2E="))
    assert f.slurs_in(f"{hard} {hard}s n1gg3r") and len(f.slurs_in(f"{hard} {hard}s n1gg3r")) == 3
    assert f.slurs_in(f"my {casual} what's good") == []
    assert f.mask(f"my {casual}") == f"my {casual}"


def test_allow_and_extra_words_and_clean():
    s = merge_incoming(strong(), {"safety": {"allowed_words": ["hell"], "extra_words": ["cringe"]}})
    f = Filter(s)
    assert f.mask("so cringe") == "so c*****"
    out = f.clean({"title": "shit happens", "tags": ["fyp", "shit", "gaming"], "n": 3})
    assert out == {"title": "s*** happens", "tags": ["fyp", "gaming"], "n": 3}
    assert Filter(merge_incoming(s, {"safety": {"enabled": False}})).mask("shit") == "shit"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_render_bleeps_blocked_word(tmp_path):
    from clipbot.editor import render
    raw = tmp_path / "raw.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=30:duration=4", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=mono", "-t", "4", "-pix_fmt", "yuv420p", str(raw)], check=True)
    s = merge_incoming(strong(), {"edit": {"layout": "blur", "preset": "ultrafast"},
                                  "safety": {"bleep_volume": 0.5, "bleep_pad_s": 0}})
    out = tmp_path / "out.mp4"
    words = [Word(1.0, 1.4, "hello"), Word(2.0, 2.5, "fuck")]
    asyncio.run(render(raw, out, 0.0, 4.0, words, "x", s, tmp_path / "w", "", ""))

    def mean_db(a, b):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(a), "-t", str(b - a), "-i", str(out),
                            "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
        return float(re.search(r"mean_volume: (-?[\d.]+|-inf) dB", r.stderr).group(1).replace("-inf", "-200"))
    assert mean_db(2.1, 2.4) > -30          # the bleep is there
    assert mean_db(0.2, 0.8) < -60          # silence elsewhere

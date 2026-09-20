import shutil
import subprocess
import time

import pytest
from pydantic import ValidationError

from clipbot.config import Settings
from clipbot.studio import BUILTIN_THEMES, STYLE_FIELDS, ThemeStore, apply_style, clean_style
from tests.test_server import running_app  # noqa: F401  (fixture)


def test_clean_and_apply_style():
    assert clean_style({"font_size": 90, "crf": 1, "intro_path": "x"}) == {"font_size": 90}
    s = apply_style(Settings(), {"font_size": 90, "highlight_color": "#FF0000", "encoder": "h264_nvenc"})
    assert s.edit.font_size == 90 and s.edit.highlight_color == "#FF0000"
    assert s.edit.encoder == Settings().edit.encoder          # non-style fields untouched
    with pytest.raises(ValidationError):
        apply_style(Settings(), {"layout": "sideways"})


def test_builtin_themes_are_valid_styles():
    for name, style in BUILTIN_THEMES.items():
        assert set(style) <= set(STYLE_FIELDS), name
        apply_style(Settings(), style)                         # validates


def test_theme_store(tmp_path):
    ts = ThemeStore(tmp_path / "themes.json")
    ts.save("Mine", {"font_size": 70, "junk": 1})
    assert ts.all()["mine"] == {"Mine": {"font_size": 70}}
    with pytest.raises(ValueError):
        ts.save("Sterling", {})                                # built-in names are taken
    assert ts.delete("Mine") and not ts.delete("Mine")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_studio_render_preview_and_apply(running_app, tmp_path):
    from clipbot.models import Candidate, Platform, SpikeEvent, Stage, StreamTarget, Word
    from tests.test_server import H
    app, c = running_app
    raw = tmp_path / "raw.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=30:duration=6", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=6", "-shortest", "-pix_fmt", "yuv420p", str(raw)],
                   check=True)
    t = StreamTarget(Platform.TWITCH, "tester", "Tester", "Just Chatting", 10)
    ev = SpikeEvent(target=t, t_wall=time.time() - 3, kind="chat", score=1, chat_rate=1, baseline=1)
    cand = Candidate(id=ev.id, event=ev, raw_path=str(raw), start_wall=ev.t_wall - 3,
                     end_wall=ev.t_wall + 3, stage=Stage.SCHEDULED,
                     words=[Word(0.5, 1.0, "hello"), Word(1.1, 1.6, "world")],
                     verdict={"title": "T", "trim_start": 0.5, "trim_end": 5.5})
    app.pipeline.store.upsert_candidate(cand)

    info = c.get(f"/api/studio/clip/{cand.id}").json()
    assert info["has_source"] and info["duration"] > 5 and info["words"][0]["w"] == "hello"
    r = c.post("/api/studio/render", headers=H, json={
        "cid": cand.id, "style": BUILTIN_THEMES["Loud"], "trim_start": 1, "trim_end": 4, "hook": "watch"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert c.get(r.json()["url"].split("?")[0]).status_code == 200          # preview served
    bad = c.post("/api/studio/render", headers=H, json={"cid": cand.id, "style": {"layout": "x"}})
    assert bad.status_code == 400
    r = c.post("/api/studio/render", headers=H, json={
        "cid": cand.id, "style": {"font_size": 70}, "trim_start": 1, "trim_end": 4, "hook": "watch",
        "apply": True})
    assert r.json()["ok"], r.text
    got = app.pipeline.store.get_candidate(cand.id)
    assert got.verdict["trim_start"] == 1 and got.post_copy["hook"] == "watch"
    assert got.post_copy["style"] == {"font_size": 70} and got.final_path

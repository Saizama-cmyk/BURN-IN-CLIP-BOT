import asyncio
import json
import subprocess

from clipbot.config import EditCfg, Settings
from clipbot.editor import ass_color, ass_inline_color, ass_time, build_ass, render
from clipbot.models import Word

WORDS = [Word(0.5 + i * 0.5, 0.9 + i * 0.5, w) for i, w in
         enumerate("this is the clip test with some words".split())]


def test_ass_colors_and_time():
    assert ass_color("#F2A93B") == "&H003BA9F2"
    assert ass_color("#FFFFFF", 0x4C) == "&H4CFFFFFF"
    assert ass_inline_color("#F2A93B") == "&H3BA9F2&"
    assert ass_time(3725.456) == "1:02:05.46"


def test_build_ass_structure():
    edit = EditCfg()
    text = build_ass(WORDS, 1.0, 4.0, edit, "@brand")
    assert f"PlayResX: {edit.width}" in text and f"PlayResY: {edit.height}" in text
    assert "Style: Watermark" in text and ",9," in text.split("Style: Watermark")[1]  # top-right
    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue: 0")]
    # words overlapping [1, 4]: every one gets its own highlighted event
    assert len(dialogues) == len([w for w in WORDS if w.end > 1.0 and w.start < 4.0])
    assert ass_inline_color(edit.highlight_color) in dialogues[0]
    assert "THIS" in text or "IS" in text  # uppercase captions by default
    wm = [l for l in text.splitlines() if "Watermark,," in l]
    assert wm and wm[0].endswith("@brand")
    # the watermark alpha follows opacity (0.7 opaque → 0x4D transparent)
    assert "&H4D" in text.split("Style: Watermark")[1].split("\n")[0]


def test_build_ass_without_captions():
    text = build_ass(WORDS, 0, 5, EditCfg(captions=False), "")
    assert "Dialogue" not in text


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                          "-show_format", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _frame_rgb(path, t, w, h, y0, y1):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", f"crop={w}:{y1 - y0}:0:{y0}", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-"], capture_output=True, check=True).stdout
    return raw


def test_render_vertical_with_audio_and_captions(lavfi_clip, tmp_path):
    s = Settings()
    words = [Word(1.0 + i * 0.6, 1.5 + i * 0.6, w) for i, w in enumerate(
        "CAPTION TEST WORKS GREAT".split())]
    out = asyncio.run(render(lavfi_clip, tmp_path / "out.mp4", 1.0, 7.0, words, "someone", s,
                             tmp_path / "work", "@chatspiked"))
    info = _probe(out)
    v = next(x for x in info["streams"] if x["codec_type"] == "video")
    a = [x for x in info["streams"] if x["codec_type"] == "audio"]
    assert (v["width"], v["height"]) == (1080, 1920)
    assert a and a[0]["codec_name"] == "aac"
    assert abs(float(info["format"]["duration"]) - 6.0) < 0.3
    assert not list((tmp_path / "work").glob("*.ass")), "temp ASS must be cleaned up"

    # captions really burned in: same render without captions differs in the caption band
    plain_cfg = s.model_copy(update={"edit": s.edit.model_copy(update={"captions": False})})
    plain = asyncio.run(render(lavfi_clip, tmp_path / "plain.mp4", 1.0, 7.0, words, "someone",
                               plain_cfg, tmp_path / "work", ""))
    y = int(s.edit.height * s.edit.caption_y_pct / 100)
    band = (y - s.edit.font_size * 2, y + s.edit.font_size // 2)
    a_px = _frame_rgb(out, 1.2, s.edit.width, s.edit.height, *band)
    b_px = _frame_rgb(plain, 1.2, s.edit.width, s.edit.height, *band)
    diff = sum(1 for i in range(0, len(a_px), 3) if abs(a_px[i] - b_px[i]) > 60)
    assert diff > 2000, f"caption band barely changed ({diff} px)"


def test_facecam_layout(lavfi_clip, tmp_path):
    s = Settings()
    s = s.model_copy(update={"edit": s.edit.model_copy(update={
        "layout": "facecam", "facecam": {"cam": [900, 0, 380, 240]}})})
    out = asyncio.run(render(lavfi_clip, tmp_path / "fc.mp4", 0.0, 4.0, [], "cam", s,
                             tmp_path / "work", ""))
    v = next(x for x in _probe(out)["streams"] if x["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)


def test_intro_is_prepended(lavfi_clip, tmp_path):
    intro = tmp_path / "intro.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:size=640x360:rate=30:duration=2", "-c:v", "libx264", str(intro)],
                   check=True)  # no audio track on purpose
    s = Settings()
    s = s.model_copy(update={"edit": s.edit.model_copy(update={"intro_path": str(intro)})})
    out = asyncio.run(render(lavfi_clip, tmp_path / "withintro.mp4", 0.0, 4.0, [], "x", s,
                             tmp_path / "work", ""))
    info = _probe(out)
    assert abs(float(info["format"]["duration"]) - 6.0) < 0.4
    assert any(x["codec_type"] == "audio" for x in info["streams"])

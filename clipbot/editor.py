"""Render the vertical, captioned clip with ffmpeg.

Layouts
  * blur    — the source scaled to cover the frame, blurred and darkened, with the source
              fitted on top in the middle.
  * facecam — when ``edit.facecam[login]`` holds a webcam box: the webcam scaled/cropped to a
              ``width × facecam_height`` panel on top, the gameplay centre-cropped below.

Captions and the watermark live in one ASS file (PlayRes = output size). ffmpeg runs with
cwd = the ASS file's folder and ``subtitles=<name>.ass`` so no Windows path escaping is
needed. Optional intro/outro clips are normalised and joined with the concat filter (silent
audio synthesised when they have none).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import EditCfg, Settings, ffmpeg_exe, ffprobe_exe
from .models import Word
from .safety import Filter
from .util import ERR_SNIPPET, run_cmd

logger = logging.getLogger("clipbot.editor")

_WATERMARK_ALIGN = {"bottom-left": 1, "bottom-center": 2, "bottom-right": 3,
                    "top-left": 7, "top-center": 8, "top-right": 9}
_CAPTION_ALIGN = 2          # ASS numpad alignment: bottom centre
_ALPHA_MAX = 255            # ASS alpha: 00 opaque … FF transparent
_BOX_FIELDS = 4             # facecam box: x, y, w, h
_HOOK_ALIGN = 8             # ASS numpad alignment: top centre
_HOOK_PAD_DIV = 4           # hook box padding = font size / 4
_HOOK_FADE_IN_MS = 150      # hook fade in/out (ASS \fad)
_HOOK_FADE_OUT_MS = 250


class RenderError(RuntimeError):
    pass


# --------------------------------------------------------------------------- codecs
def video_codec_args(encoder: str, x264_preset: str, nvenc_preset: str, crf: int) -> list[str]:
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", nvenc_preset, "-rc", "vbr", "-cq", str(crf),
                "-b:v", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", x264_preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]


# --------------------------------------------------------------------------- ASS
def _rgb(hex_color: str) -> tuple[int, int, int]:
    try:
        r, g, b = bytes.fromhex(hex_color.strip().lstrip("#"))
    except ValueError as exc:
        raise ValueError(f"colour must be #RRGGBB, got {hex_color!r}") from exc
    return r, g, b


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB → &HAABBGGRR (style colour)."""
    r, g, b = _rgb(hex_color)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def ass_inline_color(hex_color: str) -> str:
    """#RRGGBB → &HBBGGRR& (override tag colour)."""
    r, g, b = _rgb(hex_color)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def ass_time(t: float) -> str:
    """Seconds → ASS H:MM:SS.cc (centiseconds)."""
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 3600 * 100)
    m, rem = divmod(rem, 60 * 100)
    sec, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[Word], trim_start: float, trim_end: float, edit: EditCfg,
              watermark: str, hook: str = "") -> str:
    """ASS subtitles: optional hook box, word-by-word captions (current word highlighted and
    popping in), and the watermark."""
    W, H = edit.width, edit.height
    duration = max(0.0, trim_end - trim_start)
    margin_v = int(round(H * (1 - edit.caption_y_pct / 100)))
    wm_alpha = int(round((1 - edit.watermark_opacity) * _ALPHA_MAX))
    box_alpha = int(round((1 - edit.hook_box_opacity) * _ALPHA_MAX))
    style_fmt = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                 "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
                 "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
    caption_style = (f"Style: Caption,{edit.font},{edit.font_size},{ass_color(edit.text_color)},"
                     f"{ass_color(edit.highlight_color)},{ass_color(edit.outline_color)},"
                     f"{ass_color(edit.outline_color)},-1,0,0,0,100,100,0,0,1,{edit.outline},"
                     f"{edit.shadow},{_CAPTION_ALIGN},{edit.watermark_margin},"
                     f"{edit.watermark_margin},{margin_v},1")
    wm_style = (f"Style: Watermark,{edit.font},{edit.watermark_size},"
                f"{ass_color(edit.text_color, wm_alpha)},{ass_color(edit.text_color, wm_alpha)},"
                f"{ass_color(edit.outline_color, wm_alpha)},{ass_color(edit.outline_color, wm_alpha)},"
                f"-1,0,0,0,100,100,0,0,1,{max(1, edit.outline // 2)},0,"
                f"{_WATERMARK_ALIGN[edit.watermark_position]},{edit.watermark_margin},"
                f"{edit.watermark_margin},{edit.watermark_margin},1")
    # BorderStyle 3 = opaque box; libass paints the box with the outline colour
    hook_style = (f"Style: Hook,{edit.font},{edit.hook_font_size},{ass_color(edit.text_color)},"
                  f"{ass_color(edit.text_color)},{ass_color(edit.hook_box_color, box_alpha)},"
                  f"{ass_color(edit.hook_box_color, box_alpha)},-1,0,0,0,100,100,0,0,3,"
                  f"{max(1, edit.hook_font_size // _HOOK_PAD_DIV)},0,{_HOOK_ALIGN},"
                  f"{edit.watermark_margin},{edit.watermark_margin},"
                  f"{int(round(H * edit.hook_y_pct / 100))},1")
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
             "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", style_fmt,
             caption_style, wm_style, hook_style, "", "[Events]",
             "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    if watermark.strip() and duration > 0:
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(duration)},Watermark,,0,0,0,,"
                     f"{ass_escape(watermark.strip())}")
    if hook.strip() and edit.hook_text and duration > 0:
        end = min(duration, edit.hook_seconds)
        fade = f"{{\\fad({_HOOK_FADE_IN_MS},{_HOOK_FADE_OUT_MS})}}"
        lines.append(f"Dialogue: 2,{ass_time(0)},{ass_time(end)},Hook,,0,0,0,,{fade}"
                     f"{ass_escape(hook.strip())}")
    if edit.captions:
        clip_words = [Word(max(0.0, w.start - trim_start), min(duration, w.end - trim_start),
                           w.text) for w in words
                      if w.end > trim_start and w.start < trim_end and w.text.strip()]
        hi = ass_inline_color(edit.highlight_color)
        base = ass_inline_color(edit.text_color)
        pop, back = "", ""
        if edit.caption_pop:
            pct = edit.caption_pop_pct
            pop = (f"\\fscx{pct}\\fscy{pct}\\t(0,{edit.caption_pop_ms},"
                   f"\\fscx100\\fscy100)")
            back = "\\fscx100\\fscy100"
        n = edit.words_per_line
        for i in range(0, len(clip_words), n):
            chunk = clip_words[i:i + n]
            chunk_end = chunk[-1].end
            next_start = clip_words[i + n].start if i + n < len(clip_words) else None
            for j, w in enumerate(chunk):
                start = w.start
                if j + 1 < len(chunk):
                    end = max(w.end, chunk[j + 1].start)
                else:
                    end = max(chunk_end, min(next_start, duration) if next_start else chunk_end)
                if end <= start:
                    continue
                parts = []
                for k, cw in enumerate(chunk):
                    txt = ass_escape(cw.text.upper() if edit.caption_uppercase else cw.text)
                    parts.append(f"{{\\c{hi}{pop}}}{txt}{{\\c{base}{back}}}" if k == j else txt)
                lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,"
                             + " ".join(parts))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- probing
def tighten_spans(words: list[Word], trim_start: float, duration: float,
                  edit: EditCfg) -> list[tuple[float, float]]:
    """Clip-relative spans to keep, with the long silences between words removed.

    Returns [(0, duration)] when there is nothing worth cutting, so callers can treat one span
    as "no tightening happened"."""
    if not edit.tighten or not words or duration <= 0:
        return [(0.0, duration)]
    spoken = sorted((max(0.0, w.start - trim_start), min(duration, w.end - trim_start))
                    for w in words if w.end > trim_start and w.start < trim_start + duration)
    if not spoken:
        return [(0.0, duration)]
    keeps: list[tuple[float, float]] = []
    cur_a, cur_b = 0.0, spoken[0][1]
    for a, b in spoken[1:]:
        if a - cur_b > edit.gap_max_s:                  # a real pause: end the span, skip ahead
            keeps.append((cur_a, min(duration, cur_b + edit.gap_keep_s)))
            cur_a = max(0.0, a - edit.gap_keep_s)
        cur_b = max(cur_b, b)
    keeps.append((cur_a, duration if duration - cur_b <= edit.gap_max_s
                  else min(duration, cur_b + edit.gap_keep_s)))
    keeps = [(a, b) for a, b in keeps if b - a > edit.gap_keep_s]
    return keeps or [(0.0, duration)]


def remap_time(t: float, keeps: list[tuple[float, float]]) -> float:
    """A clip-relative time, as it lands in the tightened output."""
    out = 0.0
    for a, b in keeps:
        if t < a:
            return out
        if t <= b:
            return out + (t - a)
        out += b - a
    return out


def tighten_graph(keeps: list[tuple[float, float]]) -> str:
    """filter_complex prefix that cuts the kept spans out of the source and joins them."""
    parts = []
    for n, (a, b) in enumerate(keeps):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[tv{n}]")
        parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[ta{n}]")
    chain = "".join(f"[tv{n}][ta{n}]" for n in range(len(keeps)))
    parts.append(f"{chain}concat=n={len(keeps)}:v=1:a=1[tvid][taud]")
    return ";".join(parts)


@dataclass
class MediaInfo:
    duration: float
    has_audio: bool
    width: int
    height: int


async def probe_media(path: Path, settings: Settings) -> MediaInfo:
    ffprobe = ffprobe_exe(settings)
    if not ffprobe:
        raise RenderError("ffprobe not found (Settings → Paths)")
    res = await run_cmd([ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                         "-show_streams", str(path)], settings.capture.probe_timeout_s)
    if res.returncode != 0:
        raise RenderError(f"ffprobe failed on {path.name}: {res.err_text.strip()[:ERR_SNIPPET]}")
    data = json.loads(res.out_text or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return MediaInfo(duration=float(data.get("format", {}).get("duration", 0) or 0),
                     has_audio=any(s.get("codec_type") == "audio" for s in streams),
                     width=int(video.get("width", 0) or 0), height=int(video.get("height", 0) or 0))


# --------------------------------------------------------------------------- filters
def zoom_window(spike_at: float | None, trim_start: float, duration: float,
                edit: EditCfg) -> tuple[float, float] | None:
    """Punch-in window (clip-relative seconds) around the spike, or None."""
    if not edit.punch_zoom or spike_at is None:
        return None
    t0 = max(0.0, spike_at - trim_start - edit.punch_zoom_lead_s)
    t1 = min(duration, t0 + edit.punch_zoom_s)
    return (round(t0, 3), round(t1, 3)) if t1 > t0 else None


def zoom_expr(edit: EditCfg, duration: float, zoom: tuple[float, float] | None) -> str:
    """zoompan's z expression: a slow drift, with an eased punch-in at the beat."""
    terms = ["1"]
    if edit.drift and duration > 0:
        terms.append(f"{edit.drift_amount}*min(1,it/{duration:.3f})")
    if zoom:
        t0, t1 = zoom
        terms.append(f"{edit.punch_zoom_amount}"
                     f"*min(1,max(0,(it-{t0:.3f})/{edit.punch_rise_s}))"
                     f"*min(1,max(0,({t1:.3f}-it)/{edit.punch_fall_s}))")
    return "+".join(terms)


def video_filter(edit: EditCfg, facecam: list[int] | None, ass_name: str | None,
                 duration: float = 0.0, zoom: tuple[float, float] | None = None,
                 src: str = "0:v") -> str:
    W, H = edit.width, edit.height
    fl = edit.scale_flags
    if facecam and len(facecam) == _BOX_FIELDS:
        x, y, w, h = facecam
        game_h = H - edit.facecam_height
        graph = (f"[{src}]split=2[cam_in][game_in];"
                 f"[cam_in]crop={w}:{h}:{x}:{y},scale={W}:{edit.facecam_height}:"
                 f"force_original_aspect_ratio=increase:flags={fl},crop={W}:{edit.facecam_height}[cam];"
                 f"[game_in]scale={W}:{game_h}:force_original_aspect_ratio=increase:flags={fl},"
                 f"crop={W}:{game_h}[game];"
                 f"[cam][game]vstack=inputs=2")
    else:
        # the stream fills a tall box instead of floating as a small letterboxed strip:
        # scale to cover the box and crop the sides, the way short-form clips are cut now
        stage_h = max(2, int(H * edit.stage_height_pct / 100) // 2 * 2)
        stage_y = min(H - stage_h, int(H * edit.stage_top_pct / 100))
        graph = (f"[{src}]split=2[bg_in][fg_in];"
                 f"[bg_in]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                 f"gblur=sigma={edit.blur_sigma},eq=brightness={edit.background_brightness}[bg];"
                 f"[fg_in]scale={W}:{stage_h}:force_original_aspect_ratio=increase:flags={fl},"
                 f"crop={W}:{stage_h}[fg];"
                 f"[bg][fg]overlay=0:{stage_y}")
    graph += f",setsar=1,fps={edit.fps},format=yuv420p"
    if zoom or (edit.drift and edit.drift_amount > 0):
        graph += (f",zoompan=z='{zoom_expr(edit, duration, zoom)}'"
                  f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={edit.fps}")
    if edit.sharpen > 0:
        graph += f",unsharp=5:5:{edit.sharpen}:5:5:0"
    if ass_name:
        graph += f",subtitles={ass_name}"
    if edit.progress_bar and duration > 0:
        graph += (f"[vid];color=c={edit.progress_bar_color}:s={W}x{edit.progress_bar_height}"
                  f":r={edit.fps}[bar];[vid][bar]overlay=x='-w+w*t/{duration:.3f}':y=H-h"
                  f":shortest=1")
    return graph + "[v]"


def bleep_windows(words: list[Word], flt: "Filter", trim_start: float, duration: float,
                  pad: float) -> list[tuple[float, float]]:
    """Output-time spans to bleep: every blocked word inside the trim, padded a little."""
    spans = []
    for w in words:
        if flt.bad(w.text):
            a, b = w.start - trim_start - pad, w.end - trim_start + pad
            if b > 0 and a < duration:
                spans.append((max(0.0, a), min(duration, b)))
    return spans


def audio_filter(edit: EditCfg, bleeps: list[tuple[float, float]] | None = None,
                 safety=None, duration: float = 0.0, src: str = "0:a") -> str:
    norm = (f"loudnorm=I={edit.loudness_i}:TP={edit.loudness_tp}:LRA={edit.loudness_lra},"
            f"aresample={edit.audio_sample_rate}")
    if not bleeps:
        return f"[{src}]{norm}[a]"
    cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in bleeps)
    return (f"[{src}]volume=0:enable='{cond}',{norm}[a0];"
            f"sine=frequency={safety.bleep_hz}:sample_rate={edit.audio_sample_rate}"
            f":duration={duration:.3f},volume={safety.bleep_volume},volume=0:enable='not({cond})'[bp];"
            f"[a0][bp]amix=inputs=2:normalize=0:duration=first[a]")


# --------------------------------------------------------------------------- render
async def render(raw_path: Path, out_path: Path, trim_start: float, trim_end: float,
                 words: list[Word], login: str, settings: Settings, work_dir: Path,
                 watermark: str, hook: str = "", spike_at: float | None = None,
                 facecam_auto: list[int] | None = None) -> Path:
    """Render ``raw_path`` [trim_start, trim_end] to a vertical clip at ``out_path``."""
    s, edit = settings, settings.edit
    ffmpeg = ffmpeg_exe(s)
    if not ffmpeg:
        raise RenderError("ffmpeg not found (Settings → Paths)")
    duration = trim_end - trim_start
    if duration <= 0:
        raise RenderError("empty trim window")
    raw_len = duration                       # what we read from the source before tightening
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stem = out_path.stem
    facecam = None
    if edit.layout == "facecam":   # a box you set by hand wins over the detected one
        facecam = edit.facecam.get(login.lower()) or edit.facecam.get(login) or facecam_auto
        if facecam and len(facecam) != _BOX_FIELDS:
            logger.warning("facecam box for %s must be x,y,w,h; using blur", login)
            facecam = None

    # pace: drop the long silences, then work in tightened time from here on
    keeps = tighten_spans(words, trim_start, duration, edit)
    tightened = len(keeps) > 1
    if tightened:
        kept = sum(b - a for a, b in keeps)
        logger.info("%s: cut %.1fs of dead air (%d pieces)", out_path.name, duration - kept,
                    len(keeps))
        words = [Word(trim_start + remap_time(w.start - trim_start, keeps),
                      trim_start + remap_time(w.end - trim_start, keeps), w.text)
                 for w in words if w.end > trim_start and w.start < trim_end]
        if spike_at is not None:
            spike_at = trim_start + remap_time(spike_at - trim_start, keeps)
        duration = kept
        trim_end = trim_start + duration

    ass_name = None
    ass_path = work_dir / f"{stem}.ass"
    flt = Filter(s)
    bleeps = (bleep_windows(words, flt, trim_start, duration, s.safety.bleep_pad_s)
              if s.safety.enabled and s.safety.bleep else [])
    if bleeps:
        logger.info("bleeping %d word(s) in %s", len(bleeps), out_path.name)
    if edit.captions or watermark.strip() or (hook.strip() and edit.hook_text):
        if s.safety.enabled and s.safety.mask_captions:
            words = [Word(w.start, w.end, flt.mask(w.text)) for w in words]
            hook = flt.mask(hook)
        ass_text = build_ass(words, trim_start, trim_end, edit, watermark, hook)
        await asyncio.to_thread(ass_path.write_text, ass_text, "utf-8")
        ass_name = ass_path.name
    has_extras = bool(edit.intro_path or edit.outro_path)
    main_out = work_dir / f"{stem}_main.mp4" if has_extras else out_path
    vsrc, asrc = ("tvid", "taud") if tightened else ("0:v", "0:a")
    graph = (video_filter(edit, facecam, ass_name, duration,
                          zoom_window(spike_at, trim_start, duration, edit), src=vsrc)
             + ";" + audio_filter(edit, bleeps, s.safety, duration, src=asrc))
    if tightened:
        graph = tighten_graph(keeps) + ";" + graph
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{trim_start:.3f}", "-t", f"{raw_len:.3f}", "-i", str(Path(raw_path).resolve()),
            "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
            *video_codec_args(edit.encoder, edit.preset, edit.nvenc_preset, edit.crf),
            "-c:a", "aac", "-b:a", f"{edit.audio_bitrate_k}k", "-movflags", "+faststart",
            str(main_out.resolve())]
    try:
        res = await run_cmd(args, edit.render_timeout_s, cwd=str(work_dir),
                            grace_s=s.app.shutdown_timeout_s)
        if res.returncode != 0 or not main_out.exists():
            raise RenderError(f"render failed: {res.err_text.strip()[-ERR_SNIPPET:]}")
        if has_extras:
            await _join_extras(main_out, out_path, settings, work_dir)
    finally:
        ass_path.unlink(missing_ok=True)
        if has_extras:
            main_out.unlink(missing_ok=True)
    return out_path


async def _join_extras(main: Path, out: Path, settings: Settings, work_dir: Path) -> None:
    """Prepend intro / append outro, normalising size, fps and audio."""
    edit = settings.edit
    ffmpeg = ffmpeg_exe(settings)
    W, H = edit.width, edit.height
    parts: list[Path] = []
    for p in (edit.intro_path, None, edit.outro_path):
        if p is None:
            parts.append(main)
        elif p:
            path = Path(p)
            if not path.exists():
                raise RenderError(f"intro/outro not found: {path}")
            parts.append(path)
    inputs: list[str] = []
    graph: list[str] = []
    labels: list[str] = []
    idx = 0
    for n, part in enumerate(parts):
        info = await probe_media(part, settings)
        inputs += ["-i", str(part.resolve())]
        vi = idx
        idx += 1
        graph.append(f"[{vi}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                     f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={edit.fps},format=yuv420p[v{n}]")
        if info.has_audio:
            graph.append(f"[{vi}:a]aresample={edit.audio_sample_rate},"
                         f"aformat=channel_layouts=stereo[a{n}]")
        else:
            inputs += ["-f", "lavfi", "-t", f"{info.duration:.3f}", "-i",
                       f"anullsrc=r={edit.audio_sample_rate}:cl=stereo"]
            graph.append(f"[{idx}:a]anull[a{n}]")
            idx += 1
        labels.append(f"[v{n}][a{n}]")
    graph.append("".join(labels) + f"concat=n={len(parts)}:v=1:a=1[v][a]")
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *inputs,
            "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
            *video_codec_args(edit.encoder, edit.preset, edit.nvenc_preset, edit.crf),
            "-c:a", "aac", "-b:a", f"{edit.audio_bitrate_k}k", "-movflags", "+faststart",
            str(out.resolve())]
    res = await run_cmd(args, edit.render_timeout_s, cwd=str(work_dir),
                        grace_s=settings.app.shutdown_timeout_s)
    if res.returncode != 0 or not out.exists():
        raise RenderError(f"intro/outro join failed: {res.err_text.strip()[-ERR_SNIPPET:]}")

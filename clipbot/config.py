"""Settings model, persistence, paths, and OAuth token storage.

Every tunable value in BURN-IN lives here. The dashboard's Settings tab is generated from
``Settings.model_json_schema()``, so a field added to any section below appears in the UI
automatically with its ``title`` as the label and its ``description`` as the help line.

Field metadata (``json_schema_extra``):
  * ``restart``: the change only takes effect after "Restart BURN-IN".
  * ``secret``:  masked in GET /api/settings, never exported unless asked, masked values
                 sent back by the UI are ignored.
  * ``widget``:  UI hint: textarea | color | path | ollama_models | map | lines | password.

User data lives in ``%LOCALAPPDATA%\\ClipBot`` (or ``$CLIPBOT_HOME``); ``settings.json``
always sits in that home folder, while the database, tokens, logs, buffers, work files and
finished clips can be moved with the ``app.*_dir`` fields.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import secure

logger = logging.getLogger("clipbot.config")

# Brand: change COMPANY / PRODUCT here to rebrand everything user-facing.
COMPANY = "BURN-IN"
PRODUCT = "BURN-IN"
APP_NAME = PRODUCT                           # window/tray/dialog titles
FULL_NAME = PRODUCT
DATA_DIR_NAME = "ClipBot"                    # legacy folder name: keeps existing installs' data
RUN_VALUE_NAME = "ClipBot"                   # legacy HKCU Run value (one autostart entry)
MASK = "••••••••"
AUTO_LANGUAGE = "auto"   # whisper.language value meaning "detect"


# --------------------------------------------------------------------------- locations
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Folder holding bundled read-only files (dashboard static, assets)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def root_dir() -> Path:
    """BURN-IN's top folder: profiles.json and the first profile's data live here.
    ``CLIPBOT_HOME`` overrides (tests, portable use)."""
    env = os.environ.get("CLIPBOT_HOME")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / DATA_DIR_NAME


def profiles_path() -> Path:
    return root_dir() / "profiles.json"


def active_profile() -> dict | None:
    """The profile this process runs as: ``CLIPBOT_PROFILE`` or the last one signed in."""
    path = profiles_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("profiles file %s unreadable: %s", path, exc)
        return None
    wanted = os.environ.get("CLIPBOT_PROFILE") or data.get("last", "")
    profiles = data.get("profiles", [])
    return next((p for p in profiles if p.get("id") == wanted), profiles[0] if profiles else None)


def home_dir() -> Path:
    """The active profile's folder (settings.json, db, tokens, logs, clips).

    The first profile uses the top folder itself, so data from before profiles existed
    stays exactly where it was."""
    prof = active_profile()
    rel = (prof or {}).get("dir", "")
    return root_dir() / rel if rel else root_dir()


def settings_path() -> Path:
    return home_dir() / "settings.json"


# --------------------------------------------------------------------------- field helper
def F(default: Any = ..., title: str = "", description: str = "", *, restart: bool = False,
      secret: bool = False, widget: str | None = None, default_factory: Any = None,
      **constraints: Any) -> Any:
    """Field with a UI label, help text and BURN-IN metadata."""
    extra: dict[str, Any] = {}
    if restart:
        extra["restart"] = True
    if secret:
        extra["secret"] = True
    if widget:
        extra["widget"] = widget
    kw: dict[str, Any] = dict(title=title, description=description,
                              json_schema_extra=extra or None, **constraints)
    if default_factory is not None:
        return Field(default_factory=default_factory, **kw)
    return Field(default, **kw)


class Section(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


# --------------------------------------------------------------------------- sections
class AppCfg(Section):
    """How BURN-IN runs as a Windows app and where it keeps its files."""
    start_with_windows: bool = F(False, "Start with Windows",
                                 "Launch BURN-IN when you sign in (adds/removes the HKCU Run key).")
    start_minimized: bool = F(False, "Start minimized to tray",
                              "Open straight into the tray icon without showing the window.")
    autostart_silent: bool = F(True, "Run silently at sign-in",
                               "When Windows starts BURN-IN at sign-in, it runs in the background "
                               "(tray only) without opening the window.")
    close_to_tray: bool = F(True, "Close button minimizes to tray",
                            "Closing the window keeps BURN-IN running in the tray; Quit from the tray exits.")
    open_window_on_launch: bool = F(True, "Open window on launch",
                                    "Show the dashboard window when the desktop app starts.")
    data_dir: str = F("", "Data folder",
                      "Database, tokens and logs. Empty = %LOCALAPPDATA%\\ClipBot.",
                      restart=True, widget="path")
    buffer_dir: str = F("", "Buffer folder",
                        "Rolling capture segments. Empty = <data folder>\\buffer.", restart=True,
                        widget="path")
    work_dir: str = F("", "Work folder",
                      "Raw cuts and temp files. Empty = <data folder>\\work.", restart=True,
                      widget="path")
    logs_dir: str = F("", "Logs folder", "clipbot.log and rotated logs. Empty = <data folder>\\logs.",
                      restart=True, widget="path")
    models_dir: str = F("", "Speech model folder",
                        "Where the Whisper model (~1.6 GB) is downloaded. Empty = your Hugging Face cache.",
                        restart=True, widget="path")
    min_free_gb: float = F(5.0, "Keep free disk space (GB)",
                           "Capture pauses when the buffer drive has less free space than this.", ge=0, le=10000)
    clips_dir: str = F("", "Clips folder",
                       "Finished vertical clips. Empty = <data folder>\\clips.", restart=True,
                       widget="path")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = F(
        "INFO", "Log level", "How chatty clipbot.log and the console are.")
    log_max_mb: int = F(10, "Log file size (MB)", "Rotate clipbot.log after this many megabytes.",
                        ge=1, le=500, restart=True)
    log_backups: int = F(5, "Log backups kept", "Rotated log files kept next to clipbot.log.",
                         ge=0, le=50, restart=True)
    check_ffmpeg_on_start: bool = F(True, "Check for ffmpeg on start",
                                    "Verify ffmpeg/ffprobe run before starting captures; show the setup checklist if not.")
    gpu_poll_s: float = F(10.0, "GPU poll interval (s)",
                          "How often nvidia-smi is queried for the header GPU readout.", ge=2, le=600)
    shutdown_timeout_s: float = F(8.0, "Shutdown grace (s)",
                                  "Time child processes get to exit before they are killed.", ge=1, le=60)
    auto_lock_min: int = F(30, "Auto-lock after (min)",
                           "Sign out of the dashboard after this many idle minutes (0 = never).",
                           ge=0, le=1440)
    session_hours: int = F(12, "Stay signed in (hours)", "A sign-in lasts at most this long.",
                           ge=1, le=720)
    login_max_attempts: int = F(5, "Wrong passwords before lockout",
                                "Failed sign-ins allowed before a short lockout.", ge=1, le=50)
    login_lockout_s: int = F(60, "Lockout (s)", "How long sign-in is blocked after too many failures.",
                             ge=5, le=3600)
    min_password_len: int = F(8, "Minimum password length", "Shortest password a profile may use.",
                              ge=4, le=128)
    encrypt_secrets: bool = F(True, "Encrypt saved keys",
                              "Encrypt keys and tokens on disk with your Windows account (DPAPI).")
    single_instance: bool = F(True, "Single instance",
                              "A second launch focuses the running window instead of starting another copy.",
                              restart=True)


class PathsCfg(Section):
    """External programs. Leave empty to auto-detect."""
    ffmpeg: str = F("", "ffmpeg executable", "Empty = the ffmpeg found on PATH.", widget="path")
    ffprobe: str = F("", "ffprobe executable", "Empty = the ffprobe found on PATH.", widget="path")
    streamlink: str = F("", "streamlink executable",
                        "Empty = the streamlink bundled with BURN-IN (python -m streamlink in dev).",
                        widget="path")


class BrandCfg(Section):
    """Your channel identity, used in captions, watermark and templates."""
    name: str = F("ChatSpiked", "Brand name", "Shown in templates as {brand}.")
    watermark: str = F("@chatspiked", "Watermark text", "Burned into the top of every clip. Empty = none.")
    youtube_handle: str = F("", "YouTube handle", "Your @handle; {handle} in YouTube titles/captions.")
    tiktok_handle: str = F("", "TikTok handle", "Your @handle; {handle} in TikTok captions.")
    instagram_handle: str = F("", "Instagram handle", "Your @handle; {handle} in Instagram captions.")
    facebook_page_name: str = F("", "Facebook Page name", "The Page your Reels post to; {handle} on Facebook.")


class PlatformCfg(Section):
    """Credentials and targeting for one streaming platform."""
    enabled: bool = F(True, "Enabled", "Watch this platform at all.")
    client_id: str = F("", "Client ID", "From your developer app (see README).")
    client_secret: str = F("", "Client secret", "From your developer app. Stored only on this PC.",
                           secret=True, widget="password")
    slots: int = F(15, "Capture slots", "Maximum streams captured at once on this platform.",
                   ge=0, le=60)
    forced_streamers: list[str] = F(default_factory=list, title="Forced streamers",
                                    description="Always captured when live (logins, one per line).",
                                    widget="lines")
    forced_categories: list[str] = F(default_factory=list, title="Forced categories",
                                     description="Categories always scanned first (names, one per line).",
                                     widget="lines")
    blocked_streamers: list[str] = F(default_factory=list, title="Blocked streamers",
                                     description="Never captured (logins, one per line).", widget="lines")
    languages: list[str] = F(default_factory=lambda: ["en"], title="Languages",
                             description="Stream languages to include (ISO codes, one per line). Empty = all.",
                             widget="lines")
    min_viewers: int = F(0, "Minimum viewers", "Skip streams below this viewer count (forced streamers exempt).",
                         ge=0)
    fill_slots: bool = F(True, "Fill empty slots", "If too few streams clear Minimum viewers, fill the "
                         "remaining capture slots with the biggest streams left.")

    @field_validator("forced_streamers", "forced_categories", "blocked_streamers", "languages",
                     mode="before")
    @classmethod
    def _split_items(cls, v):
        """"a, b, c" typed into one line becomes three items; blanks are dropped."""
        if isinstance(v, str):
            v = [v]
        out = []
        for item in v or []:
            out += [x.strip() for x in str(item).replace("\n", ",").split(",") if x.strip()]
        return out

    @field_validator("forced_streamers", "blocked_streamers")
    @classmethod
    def _logins(cls, v: list[str]) -> list[str]:
        """Channel logins never contain spaces ("stable ronaldo" -> "stableronaldo")."""
        return [x.replace(" ", "").lower() for x in v]


class DiscoveryCfg(Section):
    """How the top live streams are chosen."""
    interval_s: int = F(45, "Refresh interval (s)", "How often the live lists are re-fetched.", ge=15, le=3600)
    top_categories: int = F(10, "Top categories", "How many of the most-watched categories are scanned.",
                            ge=1, le=50)
    streamers_per_category: int = F(3, "Streamers per category",
                                    "Top streams taken from each scanned category.", ge=1, le=20)
    fetch_limit: int = F(100, "API page size", "Streams requested per Twitch call / Kick sorted search (max 100).",
                         ge=1, le=100)
    kick_page_size: int = F(1000, "Kick v2 page size",
                            "Streams per page when Kick's v2 list is used (oldest-first, max 1000).",
                            ge=1, le=1000)
    kick_max_pages: int = F(10, "Kick v2 max pages",
                            "Pages read per refresh from Kick's v2 list before sorting by viewers.",
                            ge=1, le=100)
    http_timeout_s: float = F(15.0, "HTTP timeout (s)", "Timeout for discovery API calls.", ge=2, le=120)
    user_agent: str = F("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/128.0 Safari/537.36", "Browser user agent",
                        "Sent to Kick's public channel endpoint (chatroom id / playback URL).")


class CaptureCfg(Section):
    """The rolling stream buffer every clip is cut from."""
    quality: str = F("1080p60,1080p,720p60,720p,best", "Stream quality",
                     "streamlink quality list, first available wins.")
    segment_s: int = F(6, "Segment length (s)", "Length of each buffer file.", ge=2, le=30)
    buffer_s: int = F(150, "Buffer length (s)", "How much of each stream is kept on disk.", ge=60, le=900)
    kick_ffmpeg_fallback: bool = F(True, "Kick direct fallback",
                                   "If streamlink fails on Kick, read the channel's playback URL with ffmpeg.")
    restart_backoff_min_s: float = F(5.0, "Restart backoff min (s)", "First wait before restarting a dead capture.",
                                     ge=1, le=600)
    restart_backoff_max_s: float = F(60.0, "Restart backoff max (s)", "Longest wait between capture restarts.",
                                     ge=1, le=3600)
    stall_timeout_s: float = F(45.0, "Stall timeout (s)",
                               "Restart a capture that has written no new segment for this long.", ge=10, le=600)
    prune_interval_s: float = F(5.0, "Prune interval (s)", "How often old segments are deleted.", ge=1, le=120)
    audio_cache_s: float = F(3.0, "Audio level cache (s)", "Reuse the last loudness reading for this long.",
                             ge=0.5, le=60)
    probe_timeout_s: float = F(20.0, "Probe timeout (s)", "Timeout for one volumedetect/ffprobe call.", ge=2, le=120)
    thumb_width: int = F(480, "Live preview width (px)", "Width of the live stream preview images.",
                         ge=160, le=1920)
    thumb_max_age_s: float = F(8.0, "Live preview refresh (s)",
                               "A stream's preview image is re-grabbed at most this often.", ge=1, le=600)


class ChatCfg(Section):
    """Chat websockets (Twitch IRC + Kick Pusher)."""
    join_spacing_s: float = F(0.6, "JOIN spacing (s)", "Delay between Twitch channel joins (rate limit).",
                              ge=0.1, le=5)
    reconnect_min_s: float = F(2.0, "Reconnect backoff min (s)", "First wait after a dropped chat socket.",
                               ge=0.5, le=120)
    reconnect_max_s: float = F(60.0, "Reconnect backoff max (s)", "Longest wait between chat reconnects.",
                               ge=1, le=900)
    ping_interval_s: float = F(60.0, "Keepalive interval (s)",
                               "Send a keepalive if the socket has been quiet this long.", ge=10, le=600)


def _default_keywords() -> dict[str, float]:
    return {"clip it": 3.0, "clip that": 3.0, "KEKW": 1.0, "LUL": 1.0, "OMEGALUL": 1.5,
            "LMAO": 1.0, "W": 0.5, "no way": 1.5, "holy": 1.0, "?????": 1.0, "WTF": 1.5}


class DetectorCfg(Section):
    """Hype detection: chat-rate, keyword and audio spikes."""
    tick_s: float = F(1.0, "Tick (s)", "How often every stream is evaluated.", ge=0.2, le=10)
    window_s: float = F(10.0, "Window (s)", "Chat rate / keywords are measured over this window.", ge=3.0, le=120)
    baseline_s: float = F(300.0, "Baseline (s)", "Rolling history the window is compared against.", ge=60.0,
                          le=3600)
    min_history_s: float = F(60.0, "Warm-up (s)", "Chat history needed before a stream can spike.", ge=10, le=1800)
    std_floor: float = F(0.5, "Std-dev floor", "Minimum baseline spread so quiet chats don't explode z.",
                         ge=0.01, le=50)
    z_threshold: float = F(3.0, "Chat z threshold", "Chat-rate z-score needed to fire.", ge=0.5, le=20)
    min_msgs_per_s: float = F(1.5, "Min messages/s", "Window chat rate must also exceed this.", ge=0, le=500)
    keyword_threshold: float = F(1.0, "Keyword threshold", "Weighted keyword hits per second needed.", ge=0, le=500)
    keyword_baseline_mult: float = F(3.0, "Keyword baseline multiple",
                                     "Keyword score must also beat this multiple of its own baseline.", ge=1, le=50)
    short_keyword_len: int = F(2, "Whole-message keyword length",
                               "Keywords this short (e.g. \"W\") only count when they are the whole message.",
                               ge=1, le=10)
    audio_z_threshold: float = F(3.5, "Audio z threshold", "Loudness z-score needed for an audio spike.",
                                 ge=0.5, le=20)
    audio_history: int = F(20, "Audio history", "Recent loudness readings the audio z is computed from.",
                           ge=4, le=500)
    audio_min_samples: int = F(8, "Audio warm-up", "Loudness readings needed before audio can fire.", ge=2, le=500)
    audio_poll_s: float = F(3.0, "Audio poll (s)", "How often each buffer's loudness is sampled.", ge=0.5, le=60)
    combo_enabled: bool = F(True, "Catch combined signals", "Fire when two weaker signals line up "
                            "(chat warming up while the audio jumps, say). This is what catches "
                            "moments no single threshold would.")
    combo_mult: float = F(0.6, "Combined signal strength", "How much of each threshold a signal "
                          "needs for the combined rule. 0.6 = 60% of the way there.", ge=0.1, le=1.0)
    combo_signals: int = F(2, "Signals needed", "How many weak signals must line up.", ge=2, le=3)
    cooldown_s: float = F(90.0, "Cooldown (s)", "Minimum time between spikes on the same stream.", ge=0, le=3600)
    chat_sample_size: int = F(40, "Chat sample size", "Last N chat lines sent to the AI with each spike.",
                              ge=0, le=500)
    sparkline_points: int = F(120, "Sparkline points", "Chat-rate history points kept per monitor for the UI.",
                              ge=10, le=2000)
    keywords: dict[str, float] = F(default_factory=_default_keywords, title="Keywords and weights",
                                   description="One per line as  keyword: weight. Multi-word = substring match.",
                                   widget="map")


class ClipCfg(Section):
    """Raw cut, quality check and length limits."""
    pre_s: float = F(25.0, "Seconds before spike", "Cut starts this long before the spike.", ge=3.0, le=240)
    post_s: float = F(12.0, "Seconds after spike", "Cut ends this long after the spike.", ge=2.0, le=120)
    min_len_s: float = F(12.0, "Minimum clip length (s)", "AI trims never go shorter than this.", ge=3.0, le=120)
    max_len_s: float = F(59.0, "Maximum clip length (s)", "AI trims never go longer than this.", ge=5.0, le=180.0)
    segment_wait_s: float = F(60.0, "Wait for buffer (s)", "How long to wait for segments covering the cut.",
                              ge=5, le=600)
    raw_preset: str = F("fast", "Raw cut x264 preset", "Encoder preset for the intermediate cut.")
    raw_crf: int = F(14, "Raw cut CRF", "Quality of the intermediate cut (lower = better). Kept "
                     "near-lossless so the final render doesn't lose detail twice.", ge=0, le=51)
    qc_max_black: float = F(0.4, "QC max black fraction", "Reject captures that are black longer than this share.",
                            ge=0, le=1)
    qc_min_mean_db: float = F(-50.0, "QC min loudness (dB)", "Reject captures quieter than this mean volume.",
                              ge=-120, le=0)
    qc_min_duration_ratio: float = F(0.8, "QC min duration ratio",
                                     "Cut must be at least this share of the requested length.", ge=0.1, le=1)
    qc_black_min_s: float = F(0.5, "QC black run (s)", "Shortest black run blackdetect counts.", ge=0.05, le=10)
    keep_rejected_hours: float = F(24.0, "Keep rejected cuts (h)",
                                   "Rejected/failed cuts stay watchable this long, then are deleted (0 = delete at once).",
                                   ge=0, le=720)
    poster_width: int = F(360, "Poster width (px)", "Width of clip thumbnails in the gallery.",
                          ge=120, le=1080)


class WorkerCfg(Section):
    """Parallelism of the pipeline stages."""
    pipelines: int = F(5, "Assembly workers", "Spikes cut and quality-checked in parallel.", ge=1, le=32)
    transcribers: int = F(2, "Transcribers", "Parallel Whisper jobs (shared model).", ge=1, le=16, restart=True)
    editors: int = F(2, "Render workers", "Vertical renders in parallel.", ge=1, le=16)
    ai_stage: int = F(1, "AI stage at once", "Clips going through vision + judge + writer at the same "
                      "time. They share one GPU, so 1 is fastest overall and keeps the PC responsive.",
                      ge=1, le=8)


class WhisperCfg(Section):
    """Speech-to-text (faster-whisper)."""
    model: str = F("large-v3-turbo", "Model", "faster-whisper model name or local folder.", restart=True)
    device: Literal["cuda", "cpu", "auto"] = F("cuda", "Device",
                                               "cuda uses the GPU; falls back to CPU int8 on any CUDA error.",
                                               restart=True)
    compute_type: str = F("int8_float16", "GPU compute type", "CTranslate2 compute type on the GPU.",
                          restart=True)
    cpu_compute_type: str = F("int8", "CPU compute type", "Compute type used on CPU / after fallback.",
                              restart=True)
    language: str = F(AUTO_LANGUAGE, "Language", "auto = detect, or a fixed code like en.")
    vad: bool = F(True, "Voice activity filter", "Skip silence with Silero VAD.")
    vad_min_silence_ms: int = F(500, "VAD min silence (ms)", "Silence length that splits speech.", ge=50, le=5000)
    beam_size: int = F(5, "Beam size", "Decoding beam width (higher = slower, better).", ge=1, le=10)


DEFAULT_SYSTEM_PROMPT = """You are the clip editor for a short-form channel that reposts the best moments \
from live streams as vertical Shorts/Reels/TikToks. You decide what gets posted and where the cut \
starts and ends.

You get one candidate moment: the streamer, category, stream title, why the hype detector fired \
(chat-rate z-score, keyword score, audio z-score, keywords hit), a sample of chat, a \
timestamped transcript of the clip (seconds from the start of the raw cut) and, when available, \
a frame-by-frame description of what is visible on screen.

WHAT MAKES A CLIP WORTH POSTING. Score each of these, then weigh them up:
1. PAYOFF - something actually happens, and a stranger can tell what it was: a kill, a fail, a \
clutch, a crash, a jump scare, a big reaction, a reveal, drama, or a genuinely funny line.
2. EMOTION - somebody feels something out loud: laughing, screaming, raging, stunned silence, \
disbelief. Real emotion carries a clip even when the visuals are plain.
3. HOOK - the first second can grab a scroller. A clip that opens mid-action or mid-sentence on \
something loud beats one that opens on a menu.
4. SELF-CONTAINED - it makes sense with no context. If you need to have watched the stream for 10 \
minutes to get it, it is weak.
5. CLARITY - you can see and hear what happened; not a dark menu, not mumbling, not buried \
under music.

A funny or shocking LINE counts as a moment even if the screen is boring - a talking-head rant, a \
confession, a wild story, a roast, an argument all pass on audio alone. Only call it chat_only \
when chat is reacting to something that is NOT in this clip (a poll, a sub train, a raid, emote \
spam, an inside joke with nothing behind it). Do not reject a moment merely because the gameplay \
is static.

Judge THIS clip, not the stream around it. Use these categories:
- "moment": worth posting. verdict "pass".
- "chat_only": chat is excited but nothing in this clip's audio, speech or picture explains it. \
verdict "reject".
- "keyword_false": the keywords were a coincidence (a word in normal talk, an unrelated emote). \
verdict "reject".
- "dead_air": silence, menus, AFK, loading screens, or unintelligible audio. verdict "reject".

Score 0-10 for how well it would do as a Short. Calibrate: 8+ is a clip you would send a friend; \
6-7 is solidly postable; 5 is watchable filler; below 4 is a waste of a post. A channel needs \
volume, so pass anything genuinely entertaining - but never pass something you cannot describe \
in one sentence.

THE CUT. Pick trim_start/trim_end (seconds on the transcript clock) so that: the clip opens as \
late as possible while still setting up the payoff (usually 1-3 seconds of setup, no more), the \
payoff lands inside the first half, and it ends right after the reaction - no trailing dead air, \
no "anyway, so..." tail. Also give "peak_at": the exact second the best beat happens, which is \
where the edit punches in.

Write a punchy title (max {title_max} chars, no hashtags, no clickbait lies), a caption \
(max {caption_max} chars) and {hashtags_min}-{hashtags_max} relevant hashtags without the # sign.

Reply with ONLY this JSON object:
{"verdict": "pass"|"reject", "category": "moment"|"chat_only"|"keyword_false"|"dead_air", \
"score": 0-10, "reason": "one sentence", "title": "...", "caption": "...", \
"hashtags": ["..."], "trim_start": seconds, "trim_end": seconds, "peak_at": seconds}"""

DEFAULT_VISION_PROMPT = """These images are frames from a short clip of {streamer}'s live stream \
({category}), in time order: {frames}.

For each frame, write one or two short, literal sentences: what game or scene is shown, what is \
happening on screen (kills, deaths, wins, fails, jump scares, crashes, big plays), the streamer's \
facecam reaction if a webcam is visible, and any on-screen text or alerts (donations, subs, raids, \
polls, kill feed, scoreboard). Start each line with its timestamp, like "12.5s:".

Finish with one line starting "Overall:" that says whether something notable visibly happens and \
at which time, or that nothing notable is visible. Describe only what you can see; do not guess \
about sound."""


DEFAULT_FACECAM_PROMPT = """This is a frame from a live stream. Is there a webcam / facecam overlay showing the streamer (a person's face, usually in a corner rectangle over the game)? If the whole frame is the camera (IRL or just chatting), answer false.

Reply with ONLY JSON: {"facecam": true|false, "box": [x1, y1, x2, y2]} where the box tightly covers the webcam rectangle in coordinates from 0 to 1000 (0,0 = top-left, 1000,1000 = bottom-right). Use [] for box when facecam is false."""


class AICfg(Section):
    """The local model (Ollama) that judges clipability and writes titles."""
    ollama_url: str = F("http://127.0.0.1:11434", "Ollama URL", "Where Ollama listens.")
    model: str = F("qwen3-coder:30b", "Model", "Pick from the models installed in Ollama.", widget="ollama_models")
    temperature: float = F(0.2, "Temperature", "Lower = more consistent verdicts.", ge=0, le=2)
    min_score: int = F(6, "Minimum score", "Clips scoring below this are rejected even if the model says pass.",
                       ge=0, le=10)
    timeout_s: int = F(180, "Timeout (s)", "Give up on one model call after this long.", ge=10, le=1800)
    keep_alive: str = F("30m", "Keep model loaded", "Ollama keep_alive (e.g. 30m, 1h, -1 = forever).")
    num_ctx: int = F(8192, "Context size", "Tokens of context requested from Ollama.", ge=1024, le=262144)
    retries: int = F(1, "Retries on bad JSON", "Extra attempts before rejecting as model_error.", ge=0, le=5)
    unavailable_retry_s: float = F(30.0, "Ollama down retry (s)",
                                   "Items wait and retry this often while Ollama is unreachable.", ge=5, le=3600)
    title_max: int = F(90, "Title max chars", "Titles are cut to this length.", ge=10, le=100)
    caption_max: int = F(300, "Caption max chars", "Captions are cut to this length.", ge=20, le=2000)
    hashtags_min: int = F(3, "Min hashtags", "Fewest hashtags requested.", ge=0, le=30)
    hashtags_max: int = F(6, "Max hashtags", "Most hashtags kept.", ge=1, le=30)
    transcript_max_chars: int = F(6000, "Transcript max chars", "Longer transcripts are cut before sending.",
                                  ge=500, le=100000)
    vision_enabled: bool = F(True, "Look at the video",
                             "A vision model describes frames of each clip for the judge (slower, better calls).")
    vision_model: str = F("qwen3-vl:8b-instruct", "Vision model",
                          "Ollama vision model that describes the frames (qwen3-vl:4b is lighter).",
                          widget="ollama_models")
    vision_frames: int = F(6, "Frames per clip", "Frames sent to the vision model, spread over the clip.",
                           ge=1, le=16)
    vision_frame_width: int = F(640, "Frame width (px)", "Frames are scaled to this width before sending.",
                                ge=128, le=1920)
    vision_jpeg_quality: int = F(4, "Frame JPEG quality", "ffmpeg -q:v for frames (2 = best, 31 = smallest).",
                                 ge=2, le=31)
    vision_num_ctx: int = F(16384, "Vision context size",
                            "Tokens for frames + answer (each 640px frame costs ~1,000).",
                            ge=4096, le=131072)
    vision_temperature: float = F(0.2, "Vision temperature", "Lower = more literal descriptions.", ge=0, le=2)
    vision_keep_alive: str = F("5m", "Keep vision model loaded",
                               "Ollama keep_alive for the vision model (it shares the GPU with the judge).")
    auto_pull: bool = F(True, "Download missing models", "On start, download the Ollama models the "
                        "judge profile needs if they aren't installed yet (first-run setup).")
    pull_timeout_s: float = F(3600.0, "Model download timeout (s)", "Longest a model download may "
                              "take.", ge=60, le=86400)
    judge_profile: Literal["auto", "light", "balanced", "strong", "custom"] = F(
        "auto", "Judge profile", "auto = pick from your GPU · light = small model, stricter rules · "
        "balanced = one multimodal model does everything · strong = big separate judge · "
        "custom = use Model / One model for everything below.")
    strong_vram_gb: float = F(24.0, "Strong profile from (GB)", "Auto uses the strong profile on GPUs "
                              "with at least this much memory.", ge=4, le=200)
    balanced_vram_gb: float = F(10.0, "Balanced profile from (GB)", "Auto uses balanced from here up, "
                                "light below it.", ge=2, le=200)
    light_model: str = F("qwen3-vl:4b-instruct", "Light model", "Small multimodal model for the light profile "
                         "(empty = the vision model).", widget="ollama_models")
    light_score_bonus: float = F(1.0, "Light: stricter pass mark", "Added to Min score in the light "
                                 "profile (small models pass too much).", ge=0, le=5)
    light_rules: str = F("Be strict. Pass ONLY if something clearly happens on screen or in speech "
                         "that a stranger would stop scrolling for. Chat spam, a keyword alone, "
                         "talking with no payoff, menus or loading screens: reject. When unsure, "
                         "reject.", "Light: extra rules", "Added to the judge prompt in the light "
                         "profile.", widget="textarea")
    single_model: bool = F(True, "One model for everything", "Judge, vision and writer all use the "
                           "vision model, so Ollama never swaps models on the GPU (much faster on 16 GB "
                           "cards; the judge also gets to see the frames). Off = the judge uses Model.")
    vision_retries: int = F(2, "Vision retries", "Retry a vision call that timed out, lost its "
                            "connection or hit an Ollama error (model swaps on a busy GPU).", ge=0, le=5)
    vision_retry_wait_s: float = F(6.0, "Vision retry wait (s)", "Pause before each retry so Ollama "
                                   "can finish loading or recover.", ge=0, le=120)
    vision_disable_thinking: bool = F(True, "Skip vision 'thinking'",
                                      "Ask thinking-capable vision models to answer directly (faster).")
    vision_max_chars: int = F(4000, "Vision description max chars", "Longer descriptions are cut.",
                              ge=200, le=50000)
    facecam_prompt: str = F(DEFAULT_FACECAM_PROMPT, "Facecam finder prompt",
                            "Asks the vision model where the streamer's webcam is.", widget="textarea")
    vision_prompt: str = F(DEFAULT_VISION_PROMPT, "Vision prompt",
                           "Instructions for the vision model. {streamer} {category} {frames} are filled in.",
                           widget="textarea")
    second_look: bool = F(True, "Second look at near-misses", "A candidate that lands just under "
                          "the pass mark is judged again with more frames to look at, instead of "
                          "being thrown away. This is what stops good clips slipping through.")
    second_look_margin: float = F(1.5, "Near-miss margin", "How far below the pass mark still "
                                  "earns a second look.", ge=0.1, le=5.0)
    second_look_frames: int = F(10, "Second-look frames", "Frames the vision model looks at on the "
                                "second pass (more detail, slower).", ge=2, le=32)
    system_prompt: str = F(DEFAULT_SYSTEM_PROMPT, "Clipability system prompt",
                           "Instructions for the judge. {title_max} {caption_max} {hashtags_min} {hashtags_max} are filled in.",
                           widget="textarea")


DEFAULT_COPY_PROMPT = """You write the posts for a short-form clip channel called {brand}. You get one \
clip that already passed review: streamer, platform, category, the reviewer's title and reason, \
the transcript, what is visible on screen, and notes on what has performed best on this channel.

You are also shown frames from the clip itself (one of them at the exact moment chat \
exploded) and chat's live reaction. WATCH them. Everything you write must be about what actually \
happens in THIS clip: name the real game, the real play, the real line someone said, the real \
reaction. Quote a short line from the transcript when it lands. Never write generic filler like \
"insane moment", "you won't believe", "wait for it" unless it is literally true of this clip. \
The spike timing, z-scores and seconds are notes for you: never put timestamps ("at 25s"), \
"the spike", "chat z" or any tool jargon in the copy. Always fill "vibe". Never use slurs, \
hateful or derogatory terms, even if someone in the clip says them.

First decide the clip's vibe (hype, funny, rage, wholesome, clutch, awkward, chaos, wholesome) and \
write in that voice: hype copy hits hard and short, funny copy has timing, wholesome copy is warm. \
Sound like a real clipper who watches this streamer, not a brand account.

Write fresh copy for every platform. Tell it as a tiny story: a hook that makes people stop \
scrolling, the setup, then the payoff, without spoiling the punchline in the hook. Always credit \
the streamer by name. No false claims, no clickbait lies, no emojis spam (one or two at most).
For every platform also write "comment": the first comment the channel posts under the clip, a \
short question or hot take that makes viewers reply (never "like and subscribe").

Platform rules:
{platform_rules}

Reply with ONLY this JSON object:
{"vibe": "one word", "hook": "one-line hook", "youtube": {"title": "...", "description": "...", "tags": ["..."], "comment": "..."}, \
"tiktok": {"caption": "...", "hashtags": ["..."], "comment": "..."}, \
"instagram": {"caption": "...", "hashtags": ["..."], "comment": "..."}, \
"facebook": {"title": "...", "description": "...", "comment": "..."}, \
"discord": {"message": "..."}}"""

DEFAULT_PLATFORM_RULES = {
    "youtube": "Title under 70 characters, curiosity + the streamer's name, searchable words first; no "
               "hashtags in the title. Description: 2-3 sentences with the story and keywords, then a "
               "credit line. 5-10 tags: streamer, game/category, moment type, general ones.",
    "tiktok": "Caption under 150 characters: a punchy hook or question in casual, meme-aware voice. "
              "3-5 hashtags mixing broad (#fyp #gaming) and specific (streamer, game).",
    "instagram": "Caption 1-3 short lines telling the mini story, a line break, then a call to "
                 "action (follow for daily clips). 5-8 relevant hashtags, no banned or spammy tags.",
    "facebook": "Title under 60 characters. Description 1-2 plain sentences that set up the moment "
                "for a broader, older audience; mention the streamer and game.",
    "discord": "One short, hype message for a community channel, under 200 characters.",
}


class CopyCfg(Section):
    """Per-platform titles, descriptions, tags and story captions written by the local model."""
    enabled: bool = F(True, "Write per-platform copy",
                      "After a clip passes, write separate title/description/tags for each platform.")
    temperature: float = F(0.85, "Creativity", "Higher = more varied copy.", ge=0, le=2)
    model: str = F("", "Writer model", "Ollama model that writes titles and captions. Empty = the "
                   "vision model, so it can actually watch the clip.", widget="ollama_models")
    watch: bool = F(True, "Watch the clip", "Show the writer frames from the clip (one at the "
                    "spike) so titles describe what really happens.")
    frames: int = F(6, "Frames to watch", "How many frames the writer sees.", ge=1, le=12)
    chat_lines: int = F(15, "Chat lines", "How much of chat's reaction the writer reads.", ge=0, le=40)
    prompt: str = F(DEFAULT_COPY_PROMPT, "Copywriter prompt",
                    "{brand} and {platform_rules} are filled in; the clip details are added below it.",
                    widget="textarea")
    youtube_rules: str = F(DEFAULT_PLATFORM_RULES["youtube"], "YouTube style", "How YouTube copy should read.",
                           widget="textarea")
    tiktok_rules: str = F(DEFAULT_PLATFORM_RULES["tiktok"], "TikTok style", "How TikTok copy should read.",
                          widget="textarea")
    instagram_rules: str = F(DEFAULT_PLATFORM_RULES["instagram"], "Instagram style",
                             "How Instagram copy should read.", widget="textarea")
    facebook_rules: str = F(DEFAULT_PLATFORM_RULES["facebook"], "Facebook style",
                            "How Facebook copy should read.", widget="textarea")
    discord_rules: str = F(DEFAULT_PLATFORM_RULES["discord"], "Discord style", "How Discord messages read.",
                           widget="textarea")
    youtube_tags_max: int = F(10, "YouTube tags max", "Tags kept for YouTube.", ge=0, le=30)
    hashtags_max: int = F(8, "Hashtags max (TikTok/Instagram)", "Hashtags kept per caption.", ge=0, le=30)


class AnalyticsCfg(Section):
    """Learn from how posted clips perform and steer toward what works."""
    enabled: bool = F(True, "Track performance", "Pull views/likes/followers for posted clips.")
    interval_h: float = F(6.0, "Refresh every (h)", "Periodic analytics refresh.", ge=0.25, le=168)
    next_day_h: float = F(24.0, "Next-day check (h)",
                          "Each post is measured again once it is this old (its main score).", ge=1, le=168)
    end_of_day_hour: int = F(23, "End-of-day refresh (hour)", "Local hour for the daily summary refresh.",
                             ge=0, le=23)
    lookback_days: int = F(14, "Learn from last (days)", "Only posts this recent shape decisions.",
                           ge=1, le=365)
    min_posts: int = F(5, "Posts needed before steering", "Below this many measured posts nothing is steered.",
                       ge=1, le=500)
    shrinkage: float = F(3.0, "Confidence shrink",
                         "Groups with few posts are pulled toward average (higher = more cautious).", ge=0, le=50)
    steer_strength: float = F(0.5, "Steer strength", "0 = ignore analytics, 1 = follow them strongly.",
                              ge=0, le=1)
    explore_share: float = F(0.35, "Keep exploring",
                             "Share of picks made without analytics bias, so new things still get tried.",
                             ge=0, le=1)
    max_share_per_streamer: float = F(0.34, "Max share per streamer",
                                      "At most this share of a platform's posts per day from one streamer.",
                                      gt=0, le=1)
    max_share_per_category: float = F(0.5, "Max share per category",
                                      "At most this share of a platform's posts per day from one category.",
                                      gt=0, le=1)
    boost_top_streamers: int = F(3, "Watch top streamers first",
                                 "Best-performing streamers get priority in discovery when they are live.",
                                 ge=0, le=20)
    feed_copywriter: bool = F(True, "Tell the copywriter what works",
                              "Top titles and themes are given to the copywriter as examples.")
    examples: int = F(5, "Examples per list", "Top titles/streamers/categories named in notes and on the Analytics page.",
                      ge=1, le=50)
    insights_max_chars: int = F(1200, "Insight notes max chars", "Length of the notes given to the AI.",
                                ge=100, le=10000)
    tick_s: float = F(300.0, "Check every (s)", "How often due refreshes are looked for.", ge=30, le=3600)
    min_gap_min: float = F(60.0, "Min time between refreshes (min)",
                           "Next-day and end-of-day refreshes wait at least this long after the last one.",
                           ge=5, le=1440)


class ClipImportCfg(Section):
    """Repost the most popular viewer-made Twitch clips, with BURN-IN's edit on top."""
    enabled: bool = F(True, "Import popular Twitch clips",
                      "Pull top clips people made on Twitch and run them through the editor.")
    interval_h: float = F(3.0, "Check every (h)", "How often Twitch's top clips are pulled.", ge=0.25, le=168)
    period_h: float = F(24.0, "Clips from the last (h)", "Only clips created this recently.", ge=1, le=720)
    from_top_games: int = F(5, "Top games to scan", "Top categories whose best clips are pulled.", ge=0, le=50)
    from_watched_streamers: bool = F(True, "Scan watched streamers",
                                     "Also pull top clips of the streamers being monitored.")
    per_source: int = F(5, "Clips per game/streamer", "Best clips requested per game or streamer.",
                        ge=1, le=100)
    min_views: int = F(1000, "Minimum views", "Skip clips with fewer views.", ge=0)
    max_per_run: int = F(6, "Max clips per check", "At most this many new clips are processed per check.",
                         ge=1, le=100)
    judge: bool = F(True, "Let the AI judge them", "Run imported clips through the vision + clipability judge.")
    min_score: int = F(5, "Minimum score (imports)", "Judge score an imported clip needs to be posted.",
                       ge=0, le=10)
    credit_template: str = F("🎥 {streamer} on {platform} · clip by {clipper}", "Credit line (imports)",
                             "Credit used for imported clips: {streamer} {platform} {clipper}.")
    download_timeout_s: float = F(180.0, "Download timeout (s)", "Give up on one clip download after this.",
                                  ge=10, le=3600)


class BacklogCfg(Section):
    """Failsafe that pauses capture when the pipeline falls behind."""
    capacity: int = F(100, "Capacity", "In-flight items that count as 100 % pressure.", ge=5, le=10000)
    relief: bool = F(True, "Watch fewer streams when behind", "If the queue stays full, BURN-IN "
                     "quietly watches fewer streams until it catches up, then goes back to normal. "
                     "Without this the failsafe can sit on screen for hours.")
    relief_after_s: float = F(180.0, "Back off after (s)", "How long the queue has to stay full "
                              "before it starts watching fewer streams.", ge=30, le=3600)
    relief_step: int = F(3, "Streams dropped each time", "How many streams to drop per step while "
                         "catching up.", ge=1, le=20)
    relief_floor: int = F(4, "Never go below", "Streams it keeps watching no matter how far "
                          "behind it is.", ge=1, le=50)
    pause_at: float = F(0.8, "Pause at", "Pressure at which all captures pause and spikes are ignored.",
                        gt=0, le=1.0)
    resume_at: float = F(0.5, "Resume at", "Pressure at which captures resume (must be below Pause at).",
                         ge=0, lt=1.0)

    @model_validator(mode="after")
    def _order(self) -> "BacklogCfg":
        if self.resume_at >= self.pause_at:
            raise ValueError("resume_at must be lower than pause_at")
        return self


class EditCfg(Section):
    """The 9:16 render: layout, encoder, captions, watermark."""
    layout: Literal["facecam", "blur"] = F("facecam", "Layout",
                                           "facecam = webcam panel on top when the streamer has a box below, else blur; blur = always blurred fill.")
    facecam: dict[str, list[int]] = F(default_factory=dict, title="Facecam boxes",
                                      description="login: x,y,w,h of the webcam in the source (one per line).",
                                      widget="map")
    width: int = F(1080, "Output width", "Vertical frame width.", ge=360, le=2160)
    height: int = F(1920, "Output height", "Vertical frame height.", ge=640, le=3840)
    fps: int = F(30, "Output FPS", "Frame rate of rendered clips.", ge=15, le=60)
    facecam_height: int = F(608, "Facecam panel height", "Height of the top webcam panel in facecam layout.",
                            ge=100, le=1600)
    stage_height_pct: float = F(64.0, "Stage height (%)", "How much of the vertical frame the "
                                "stream fills in blur layout. Higher = bigger picture, more of the "
                                "sides cropped away. 100 = edge to edge.", ge=30, le=100)
    stage_top_pct: float = F(20.0, "Stage position (%)", "Where the top of that picture sits, % "
                             "down the frame. Leave room underneath for the captions.",
                             ge=0, le=60)
    blur_sigma: float = F(30.0, "Background blur", "Gaussian blur strength of the fill.", ge=1, le=200)
    background_brightness: float = F(-0.25, "Background brightness", "Darkening of the blurred fill (-1..0).",
                                     ge=-1, le=0)
    encoder: Literal["libx264", "h264_nvenc"] = F("libx264", "Encoder", "libx264 (CPU) or h264_nvenc (GPU).")
    preset: str = F("medium", "x264 preset", "libx264 preset (ultrafast … veryslow); slower = sharper.")
    nvenc_preset: str = F("p5", "NVENC preset", "h264_nvenc preset (p1 fastest … p7 best).")
    crf: int = F(17, "Quality (CRF/CQ)", "Lower = better quality, bigger files.", ge=0, le=51)
    audio_sample_rate: int = F(48000, "Audio sample rate", "Output audio sample rate (Hz).", ge=8000,
                               le=96000)
    audio_bitrate_k: int = F(192, "Audio bitrate (kbps)", "AAC bitrate.", ge=32, le=512)
    loudness_i: float = F(-14.0, "Loudness target (LUFS)", "loudnorm integrated loudness.", ge=-40, le=-5)
    loudness_tp: float = F(-1.5, "True peak (dBTP)", "loudnorm true-peak ceiling.", ge=-9, le=0)
    loudness_lra: float = F(11.0, "Loudness range", "loudnorm LRA.", ge=1, le=20)
    captions: bool = F(True, "Burn captions", "Word-by-word captions from the transcript.")
    font: str = F("Arial Black", "Caption font", "Installed font family name.")
    font_size: int = F(78, "Caption size", "Caption font size at 1080x1920.", ge=10, le=300)
    text_color: str = F("#FFFFFF", "Caption color", "Caption text color.", widget="color")
    highlight_color: str = F("#F2A93B", "Highlight color", "Color of the word being spoken.", widget="color")
    outline_color: str = F("#000000", "Outline color", "Caption outline color.", widget="color")
    outline: int = F(6, "Outline width", "Caption outline thickness.", ge=0, le=30)
    shadow: int = F(0, "Shadow", "Caption drop shadow depth.", ge=0, le=20)
    words_per_line: int = F(3, "Words per caption", "Words shown at a time.", ge=1, le=8)
    caption_y_pct: float = F(72.0, "Caption position (%)", "Caption baseline, % down the frame.", ge=5, le=98)
    caption_uppercase: bool = F(True, "Uppercase captions", "Render caption text in capitals.")
    watermark_position: Literal["top-right", "top-left", "top-center", "bottom-right",
                                "bottom-left", "bottom-center"] = F(
        "top-right", "Watermark position", "Corner the brand watermark sits in.")
    watermark_opacity: float = F(0.7, "Watermark opacity", "0 = invisible, 1 = solid.", ge=0, le=1)
    watermark_size: int = F(44, "Watermark size", "Watermark font size.", ge=8, le=200)
    watermark_margin: int = F(48, "Watermark margin", "Distance from the frame edge (px).", ge=0, le=500)
    intro_path: str = F("", "Intro clip", "Video prepended to every clip. Empty = none.", widget="path")
    outro_path: str = F("", "Outro clip", "Video appended to every clip. Empty = none.", widget="path")
    render_timeout_s: float = F(600.0, "Render timeout (s)", "Kill a render that takes longer.", ge=30, le=7200)
    keep_raw: bool = F(True, "Keep source cuts", "Keep each clip's source cut after rendering so "
                       "Studio can restyle and re-trim it. Removed with the clip when it expires.")
    scale_flags: Literal["lanczos", "bicubic", "bilinear"] = F(
        "lanczos", "Scaling filter", "How frames are resized (lanczos = sharpest).")
    sharpen: float = F(0.35, "Sharpen", "Mild unsharp mask after scaling (0 = off).", ge=0, le=2)
    hook_text: bool = F(True, "Hook text", "Show the copywriter's hook line at the start of the clip.")
    hook_seconds: float = F(2.8, "Hook duration (s)", "How long the hook stays on screen.", ge=0.5, le=10)
    hook_font_size: int = F(64, "Hook size", "Hook text size at 1080x1920.", ge=10, le=200)
    hook_y_pct: float = F(13.0, "Hook position (%)", "Hook box position, % down the frame.", ge=2, le=90)
    hook_box_color: str = F("#0B1522", "Hook box color", "Background box behind the hook text.",
                            widget="color")
    hook_box_opacity: float = F(0.78, "Hook box opacity", "0 = no box, 1 = solid.", ge=0, le=1)
    tighten: bool = F(True, "Cut the dead air", "Silently removes the long pauses inside a clip "
                      "(the 'uhh', the walk back to spawn) and joins it back together. Short-form "
                      "clips live or die on pace; this is the single biggest retention win.")
    gap_max_s: float = F(0.9, "Longest pause kept (s)", "Silence longer than this is cut down.",
                         ge=0.2, le=6.0)
    gap_keep_s: float = F(0.22, "Breath kept (s)", "How much silence stays on each side of a cut "
                          "so speech doesn't sound clipped.", ge=0.0, le=2.0)
    drift: bool = F(True, "Slow push-in", "A slow, constant zoom across the clip so the frame is "
                    "never completely still. Standard in 2026 short-form edits.")
    drift_amount: float = F(0.035, "Push-in amount", "How far the slow zoom travels (0.035 = 3.5%).",
                            ge=0.0, le=0.3)
    punch_rise_s: float = F(0.16, "Punch-in ramp (s)", "How fast the zoom snaps in at the big beat.",
                            ge=0.02, le=2.0)
    punch_fall_s: float = F(0.4, "Punch-out ramp (s)", "How fast it eases back out.", ge=0.02, le=4.0)
    punch_zoom: bool = F(True, "Punch-in zoom", "Zoom in on the moment itself (at the spike).")
    punch_zoom_amount: float = F(0.12, "Zoom amount", "How far to zoom in (0.12 = 12 %).", ge=0.01, le=1)
    punch_zoom_s: float = F(1.6, "Zoom duration (s)", "How long the punch-in lasts.", ge=0.2, le=10)
    punch_zoom_lead_s: float = F(0.3, "Zoom lead (s)", "Start the zoom this long before the spike.",
                                 ge=0, le=5)
    caption_pop: bool = F(True, "Pop captions", "The spoken word pops in slightly bigger.")
    caption_pop_pct: int = F(118, "Pop size (%)", "Scale of the word as it pops in.", ge=100, le=200)
    caption_pop_ms: int = F(120, "Pop duration (ms)", "How long the pop takes to settle.", ge=20, le=1000)
    progress_bar: bool = F(True, "Progress bar", "Thin bar along the bottom showing clip progress.")
    progress_bar_height: int = F(8, "Progress bar height (px)", "Height of the progress bar.", ge=1, le=60)
    progress_bar_color: str = F("#F2A93B", "Progress bar color", "Color of the progress bar.",
                                widget="color")
    auto_facecam: bool = F(True, "Find facecam automatically",
                           "The vision model locates the streamer's webcam for the facecam layout.")
    auto_facecam_refresh_h: float = F(24.0, "Re-check facecam after (h)",
                                      "Streamers' webcam positions are re-detected after this long.",
                                      ge=1, le=720)
    facecam_min_area_pct: float = F(1.0, "Facecam min size (%)",
                                    "Ignore detections smaller than this share of the frame.", ge=0.1, le=50)
    facecam_max_area_pct: float = F(35.0, "Facecam max size (%)",
                                    "Ignore detections bigger than this share of the frame.", ge=1, le=90)


class YouTubePostCfg(Section):
    """YouTube Shorts."""
    enabled: bool = F(False, "Post to YouTube", "Upload finished clips as Shorts.")
    daily_cap: int = F(6, "Daily cap", "Maximum uploads per 24 h.", ge=0, le=100)
    min_gap_min: int = F(60, "Minimum gap (min)", "Minutes between uploads.", ge=0, le=1440)
    privacy: Literal["public", "unlisted", "private"] = F("public", "Privacy", "Visibility of new Shorts.")
    age_restrict: bool = F(True, "18+ (age-restricted)", "Mark uploads 18+ on YouTube. Stream clips "
                           "carry swearing and adult humour, so this is on by default; it keeps the "
                           "video off kids' feeds and out of Made for Kids treatment.")
    category_id: str = F("20", "Category ID", "YouTube category (20 = Gaming).")
    shorts_suffix: str = F(" #Shorts", "Title suffix", "Appended to titles (keeps the Shorts shelf).")


class TikTokPostCfg(Section):
    """TikTok (Content Posting API)."""
    enabled: bool = F(False, "Post to TikTok", "Upload finished clips to TikTok.")
    daily_cap: int = F(15, "Daily cap", "Maximum uploads per 24 h.", ge=0, le=100)
    min_gap_min: int = F(30, "Minimum gap (min)", "Minutes between uploads.", ge=0, le=1440)
    privacy: Literal["SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE"] = F(
        "SELF_ONLY", "Privacy", "Unaudited apps can only post SELF_ONLY.")
    chunk_mb: int = F(10, "Upload chunk (MB)", "Chunk size for the upload PUTs.", ge=5, le=64)


class InstagramPostCfg(Section):
    """Instagram Reels (Instagram Login API)."""
    enabled: bool = F(False, "Post to Instagram", "Publish finished clips as Reels.")
    daily_cap: int = F(25, "Daily cap", "Maximum Reels per 24 h (API limit 50).", ge=0, le=50)
    min_gap_min: int = F(30, "Minimum gap (min)", "Minutes between Reels.", ge=0, le=1440)
    share_to_feed: bool = F(True, "Share to feed", "Also show the Reel on the profile grid.")
    token_refresh_days: int = F(30, "Refresh token after (days)",
                                "Long-lived tokens last 60 days; BURN-IN renews them after this many.",
                                ge=1, le=59)


class FacebookPostCfg(Section):
    """Facebook Page Reels."""
    enabled: bool = F(False, "Post to Facebook", "Publish finished clips as Page Reels.")
    daily_cap: int = F(25, "Daily cap", "Maximum Reels per 24 h.", ge=0, le=100)
    min_gap_min: int = F(30, "Minimum gap (min)", "Minutes between Reels.", ge=0, le=1440)


class DiscordPostCfg(Section):
    """Discord webhook."""
    enabled: bool = F(False, "Post to Discord", "Send finished clips to a Discord channel.")
    daily_cap: int = F(200, "Daily cap", "Maximum messages per 24 h.", ge=0, le=5000)
    min_gap_min: int = F(0, "Minimum gap (min)", "Minutes between messages.", ge=0, le=1440)
    max_file_mb: float = F(24.0, "Max attachment (MB)", "Bigger clips are sent as text only.", ge=1, le=500)
    username: str = F("BURN-IN Clips", "Webhook username", "Name shown on webhook messages.")


class PostingCfg(Section):
    """Scheduling, templates and per-platform posting rules."""
    youtube: YouTubePostCfg = Field(default_factory=YouTubePostCfg, title="YouTube")
    tiktok: TikTokPostCfg = Field(default_factory=TikTokPostCfg, title="TikTok")
    instagram: InstagramPostCfg = Field(default_factory=InstagramPostCfg, title="Instagram")
    facebook: FacebookPostCfg = Field(default_factory=FacebookPostCfg, title="Facebook")
    discord: DiscordPostCfg = Field(default_factory=DiscordPostCfg, title="Discord")
    hours_start: int = F(0, "Posting window start (hour)", "Local hour posting may start (0-23).", ge=0, le=23)
    hours_end: int = F(24, "Posting window end (hour)", "Local hour posting stops (1-24). Start=0,End=24 = always.",
                       ge=1, le=24)
    title_template: str = F("{title}", "Title template",
                            "Placeholders: {title} {streamer} {platform} {category} {hashtags} {brand} {handle}.")
    caption_template: str = F("{caption}\n\n{credit}\n{hashtags}", "Caption template",
                              "Placeholders: {caption} {credit} {title} {streamer} {platform} {category} {hashtags} {brand} {handle}.",
                              widget="textarea")
    credit_line: bool = F(True, "Credit the streamer", "Fill {credit} with the credit template.")
    credit_template: str = F("🎥 {streamer} on {platform}", "Credit template", "Text used for {credit}.")
    tick_s: float = F(20.0, "Scheduler tick (s)", "How often the queue is checked.", ge=2, le=600)
    prefer_score_after_min: float = F(60.0, "Prefer score after (min)",
                                      "Once clips have waited this long, post the highest-scoring first.",
                                      ge=0, le=10080)
    retention_days: int = F(7, "Retention (days)", "Clips, samples and post records older than this are removed.",
                            ge=1, le=365)
    delete_remote_on_expiry: bool = F(False, "Delete remote posts on expiry",
                                      "Also delete the uploaded post where the platform allows it.")
    cleanup_interval_s: float = F(3600.0, "Cleanup interval (s)", "How often expired items are removed.",
                                  ge=60, le=86400)
    max_attempts: int = F(2, "Attempts per platform", "Upload attempts per clip and platform before giving up.",
                          ge=1, le=10)
    upload_timeout_s: float = F(300.0, "Upload timeout (s)", "Timeout for one upload request.", ge=10, le=3600)
    status_poll_s: float = F(5.0, "Status poll (s)", "How often processing status is polled after upload.",
                             ge=1, le=120)
    status_poll_max: int = F(60, "Status polls max", "Give up waiting for processing after this many polls.",
                             ge=1, le=1000)


class AccountsCfg(Section):
    """Posting account keys and tokens. Stored only in settings.json on this PC."""
    youtube_client_id: str = F("", "YouTube OAuth client ID", "Google Cloud OAuth client (Desktop or Web app).")
    youtube_client_secret: str = F("", "YouTube OAuth client secret", "From the same Google OAuth client.",
                                   secret=True, widget="password")
    tiktok_client_key: str = F("", "TikTok client key", "From your TikTok developer app.")
    tiktok_client_secret: str = F("", "TikTok client secret", "From your TikTok developer app.", secret=True,
                                  widget="password")
    tiktok_redirect_uri: str = F("", "TikTok redirect URI",
                                 "Exactly as registered in the TikTok app, e.g. https://you.github.io/clipbot/callback/")
    instagram_user_id: str = F("", "Instagram user ID (optional)",
                               "Leave empty: BURN-IN looks it up from the token.")
    instagram_token: str = F("", "Instagram access token",
                             "From Meta app → Instagram → API setup with Instagram login → Generate token.",
                             secret=True, widget="password")
    facebook_page_id: str = F("", "Facebook Page ID", "Numeric ID of the Page (shown in GET /me/accounts).")
    facebook_page_token: str = F("", "Facebook Page token",
                                 "Page access_token from GET /me/accounts made with a long-lived user token.",
                                 secret=True, widget="password")
    discord_webhook: str = F("", "Discord webhook URL", "Channel Settings → Integrations → Webhooks.", secret=True,
                             widget="password")


class DashboardCfg(Section):
    """The local dashboard server."""
    host: str = F("127.0.0.1", "Host", "Interface the dashboard binds to. Keep 127.0.0.1 (never exposed).",
                  restart=True)
    port: int = F(8787, "Port", "Dashboard port.", ge=1024, le=65535, restart=True)
    remote: bool = F(False, "Phone remote", "Answer on your home Wi-Fi as well as this PC, so your "
                     "phone can run BURN-IN from the sofa. Your profile password is still required "
                     "to get in. Leave this off on networks you do not trust (cafes, hotels, "
                     "campus Wi-Fi).", restart=True)
    refresh_ms: int = F(1500, "Refresh interval (ms)", "How often the dashboard polls /api/state.",
                        ge=250, le=60000)
    samples_limit: int = F(100, "Samples shown", "Recent samples listed in the dashboard.", ge=10, le=2000)
    posts_limit: int = F(30, "Posts shown", "Recent posts listed in the dashboard.", ge=5, le=1000)
    stage_window_h: float = F(24.0, "Stage counts window (h)", "Pipeline counts cover this many hours.",
                              ge=1, le=720)
    open_browser: bool = F(False, "Open browser in dev mode",
                           "python -m clipbot opens the dashboard in your browser.")
    panel_log_kb: int = F(512, "System panel log size (KB)", "How much of the log the System "
                          "panel reads.", ge=32, le=16384)
    panel_entries: int = F(200, "System panel entries", "Events/issues listed in the System panel.",
                           ge=20, le=2000)
    panel_samples: int = F(1000, "System panel clips", "Recent clips used for its analytics.",
                           ge=50, le=20000)
    ui_sounds: bool = F(True, "App sounds", "Sound cues for publishing, renders, clicks and problems.")
    ui_volume: float = F(0.35, "App volume", "0 = silent, 1 = full.", ge=0, le=1)
    window_width: int = F(1440, "Window width", "Initial desktop window width.", ge=640, le=7680)
    window_height: int = F(900, "Window height", "Initial desktop window height.", ge=480, le=4320)
    window_min_width: int = F(900, "Window min width", "Smallest the window can be resized to.",
                              ge=480, le=7680, restart=True)
    window_min_height: int = F(600, "Window min height", "Smallest the window can be resized to.",
                               ge=360, le=4320, restart=True)


# Original looks. Each only states what it changes; everything else keeps your settings.
STUDIO_THEMES: dict[str, dict] = {
    "Sterling": {"font": "Arial Black", "text_color": "#FFFFFF", "highlight_color": "#D9DDE4",
                 "outline_color": "#000000", "outline": 7, "caption_uppercase": True,
                 "hook_box_color": "#0A0A0C", "hook_box_opacity": 0.85,
                 "progress_bar": True, "progress_bar_color": "#E6E8EC", "punch_zoom": True},
    "Ember": {"font": "Impact", "text_color": "#FFFFFF", "highlight_color": "#FF8A3D",
              "outline_color": "#120600", "outline": 6, "font_size": 84, "caption_pop": True,
              "caption_pop_pct": 124, "hook_box_color": "#1A0A02", "progress_bar_color": "#FF8A3D"},
    "Loud": {"font": "Arial Black", "font_size": 96, "words_per_line": 2, "text_color": "#FFFFFF",
             "highlight_color": "#FFE14D", "outline": 9, "caption_pop": True, "caption_pop_pct": 132,
             "caption_y_pct": 64.0, "punch_zoom": True, "punch_zoom_amount": 0.18},
    "Clean": {"font": "Segoe UI Semibold", "font_size": 66, "words_per_line": 4, "outline": 3,
              "caption_uppercase": False, "caption_pop": False, "punch_zoom": False,
              "highlight_color": "#FFFFFF", "hook_box_opacity": 0.6, "progress_bar": False},
    "Night Shift": {"font": "Bahnschrift", "text_color": "#F2EEFF", "highlight_color": "#B78CFF",
                    "outline_color": "#12071F", "outline": 6, "hook_box_color": "#12071F",
                    "progress_bar_color": "#B78CFF", "background_brightness": -0.35},
    "Cinema": {"layout": "blur", "background_brightness": -0.45, "blur_sigma": 40.0,
               "font": "Georgia", "font_size": 60, "caption_uppercase": False, "outline": 2,
               "shadow": 3, "caption_y_pct": 84.0, "caption_pop": False, "punch_zoom": False,
               "hook_text": False, "progress_bar": False},
}


class UpdatesCfg(Section):
    """Automatic updates from GitHub Releases."""
    repo: str = F("", "GitHub repo", "owner/name of the GitHub repository publishing releases. "
                  "Empty uses the source bundled with this build, if any.")
    check_on_start: bool = F(True, "Check on start", "Look for a newer version when BURN-IN starts.")
    installer_asset: str = F("BURN-IN-Setup.exe", "Installer file name",
                             "Name of the installer attached to each release.")
    timeout_s: float = F(15.0, "Check timeout (s)", "How long to wait for GitHub.", ge=2, le=120)
    download_timeout_s: float = F(900.0, "Download timeout (s)", "Longest the download may take.",
                                  ge=30, le=7200)
    notes_max: int = F(4000, "Release notes shown", "Characters of release notes shown in the app.",
                       ge=100, le=50000)


class ViewerCfg(Section):
    """The live viewer: click a monitor to watch exactly what BURN-IN is capturing."""
    segments_ahead: int = F(4, "Segments offered", "How many of the newest buffer segments the "
                            "viewer can pull (more = smoother start, further behind live).", ge=2, le=20)
    chat_lines: int = F(30, "Chat lines", "Recent chat messages shown next to the video.", ge=0, le=40)
    clips_shown: int = F(8, "Clips shown", "Clips from this stream listed in the viewer.", ge=0, le=50)
    poll_ms: int = F(1500, "Update interval (ms)", "How often the viewer refreshes.", ge=500, le=10000)


class SafetyCfg(Section):
    """Keep slurs and hateful language out of everything BURN-IN posts."""
    enabled: bool = F(True, "Content filter", "Mask blocked words in titles, captions, hashtags, "
                      "comments and on-screen captions.")
    level: Literal["slurs", "strong", "all"] = F(
        "slurs", "What to block", "slurs = racial, homophobic and other slurs · strong = slurs plus "
        "strong profanity · all = every swear word too.")
    skip_slur_clips: bool = F(True, "Skip clips with slurs", "Never post a clip where someone says "
                              "a slur (protects your channels from strikes).")
    mask_captions: bool = F(True, "Mask on-screen captions", "Blocked words in burned-in captions "
                            "show as f*** instead.")
    bleep: bool = F(True, "Bleep blocked words", "Mute each blocked word in the audio and play a "
                    "bleep over it (uses the word timings from the transcript).")
    bleep_hz: int = F(1000, "Bleep pitch (Hz)", "Tone of the bleep.", ge=200, le=4000)
    bleep_volume: float = F(0.25, "Bleep volume", "0 = silent mute only, 1 = loud.", ge=0, le=1)
    bleep_pad_s: float = F(0.06, "Bleep padding (s)", "Extra time covered before and after each "
                           "word, since word timings are never perfect.", ge=0, le=0.5)
    extra_words: list[str] = F(default_factory=list, title="Also block",
                               description="Your own words to block (one per line). Also treated "
                               "as slurs for skipping clips.", widget="lines")
    allowed_words: list[str] = F(default_factory=list, title="Always allow",
                                 description="Words the filter should never touch (one per line).",
                                 widget="lines")


class StudioCfg(Section):
    """The Studio editor: restyle clips and save looks as themes."""
    preview_preset: str = F("ultrafast", "Preview speed preset", "x264 preset for Studio previews "
                            "(fast to render; the real render uses Editor settings).")
    preview_crf: int = F(26, "Preview quality (CRF)", "Higher = smaller, faster previews.", ge=14, le=40)
    font_choices: list[str] = F(default_factory=lambda: [
        "Arial Black", "Impact", "Bahnschrift", "Segoe UI Semibold", "Georgia", "Verdana",
        "Trebuchet MS", "Comic Sans MS", "Consolas"],
        title="Font list", description="Fonts offered in Studio (installed Windows fonts).",
        widget="lines")


class PetsCfg(Section):
    """Desktop companion: a little pixel pet that lives on your desktop while BURN-IN runs
    (window open or closed to the tray), reacts to what the bot does, and shows stats on hover."""
    enabled: bool = F(True, "Show desktop pet", "A pet on your desktop while BURN-IN runs, even with "
                      "the window closed to the tray. Turns on and off instantly.")
    species: Literal["blip", "ember", "moss", "nib", "glitch"] = F(
        "blip", "Pet", "blip = tiny CRT screen · ember = flame wisp · moss = sprout blob · "
        "nib = headphone bird · glitch = scanline ghost.")
    name: str = F("", "Pet name", "Shown on the hover card. Empty = the pet's own name.", max_length=24)
    scale: int = F(3, "Size", "Pixel size of the pet (2 = small, 5 = big).", ge=2, le=6)
    wander: bool = F(True, "Wander around", "Walks along the bottom of your screen on its own.")
    activity: float = F(0.5, "Activity", "How often it does things on its own (0 = calm, 1 = hyper).",
                        ge=0, le=1)
    gravity: bool = F(True, "Falls when dropped", "Dropped mid-screen, it falls back to the taskbar.")
    sleep_after_min: float = F(10.0, "Naps after (min)", "Falls asleep after this long with nothing "
                               "happening. 0 = never.", ge=0, le=240)
    always_on_top: bool = F(True, "Always on top", "Keep the pet above other windows.")
    moodlets: bool = F(True, "Moodlets", "Little status bubbles for what BURN-IN is doing.")
    moodlet_style: Literal["bubble", "badge", "icon"] = F(
        "bubble", "Moodlet style", "bubble = icon + text · badge = compact pill · icon = icon only.")
    moodlet_seconds: float = F(6.0, "Moodlet time (s)", "How long each bubble stays.", ge=1, le=60)
    on_clip: bool = F(True, "React to new clips", "Moodlet + happy hop when a clip passes the judge.")
    on_post: bool = F(True, "React to posts", "Moodlet + dance when a clip is posted.")
    on_reject: bool = F(False, "React to rejects", "Moodlet when the judge turns a moment down.")
    on_spike: bool = F(True, "React to spikes", "Moodlet when chat spikes on a stream.")
    on_trouble: bool = F(True, "React to trouble", "Moodlet when capture pauses (failsafe, disk) "
                         "or something errors.")
    custom_accent: bool = F(False, "Custom accent", "Use your own accent color instead of the pet's.")
    accent_color: str = F("#F2A93B", "Accent color", "Moodlet and card accent when Custom accent is on.",
                          widget="color", pattern=r"^#[0-9A-Fa-f]{6}$")
    card_stats: bool = F(True, "Card: stats", "Clips, posts, queue and GPU on the hover card.")
    card_trending: bool = F(True, "Card: trending stream", "The hottest stream right now, with a "
                            "live thumbnail.")
    card_graphs: bool = F(True, "Card: graphs", "Chat-rate trace, clips per hour and posts per "
                          "platform.")
    sounds: bool = F(True, "Pet sounds", "Little chimes with the pet's reactions.")
    volume: float = F(0.45, "Pet volume", "0 = silent, 1 = full.", ge=0, le=1)
    poll_ms: int = F(2000, "Update interval (ms)", "How often the pet checks on BURN-IN.",
                     ge=500, le=60000)
    fps: int = F(14, "Animation speed (fps)", "Sprite frames per second. 12-15 looks hand-drawn, "
                 "higher is smoother and costs a little more CPU.", ge=6, le=30)
    walk_px_s: float = F(90.0, "Walk speed (px/s)", "How fast it strolls across your desktop.",
                         ge=10, le=600)
    fall_px_s2: float = F(2200.0, "Fall speed (px/s2)", "How hard it drops when you let go of it "
                          "mid-screen.", ge=200, le=8000)
    input_poll_ms: int = F(16, "Mouse polling (ms)", "How often the pet checks where your mouse is. "
                           "Lower = snappier dragging, slightly more CPU.", ge=8, le=100)
    hover_ms: int = F(400, "Hover delay (ms)", "How long to rest the mouse on the pet before its "
                      "card opens.", ge=0, le=3000)
    leave_ms: int = F(250, "Close delay (ms)", "How long after the mouse leaves before the card "
                      "closes.", ge=0, le=3000)
    double_click_ms: int = F(400, "Double-click time (ms)", "Two clicks inside this open BURN-IN.",
                             ge=120, le=1200)
    drag_slop_px: int = F(4, "Drag threshold (px)", "Move further than this and it counts as a drag, "
                          "not a click.", ge=1, le=40)


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    app: AppCfg = Field(default_factory=AppCfg, title="App")
    paths: PathsCfg = Field(default_factory=PathsCfg, title="Paths")
    brand: BrandCfg = Field(default_factory=BrandCfg, title="Brand")
    twitch: PlatformCfg = Field(default_factory=PlatformCfg, title="Twitch")
    kick: PlatformCfg = Field(default_factory=PlatformCfg, title="Kick")
    discovery: DiscoveryCfg = Field(default_factory=DiscoveryCfg, title="Discovery")
    capture: CaptureCfg = Field(default_factory=CaptureCfg, title="Capture")
    chat: ChatCfg = Field(default_factory=ChatCfg, title="Chat")
    detector: DetectorCfg = Field(default_factory=DetectorCfg, title="Detector")
    clip: ClipCfg = Field(default_factory=ClipCfg, title="Clip")
    workers: WorkerCfg = Field(default_factory=WorkerCfg, title="Workers")
    whisper: WhisperCfg = Field(default_factory=WhisperCfg, title="Whisper")
    ai: AICfg = Field(default_factory=AICfg, title="AI")
    backlog: BacklogCfg = Field(default_factory=BacklogCfg, title="Backlog")
    edit: EditCfg = Field(default_factory=EditCfg, title="Editor")
    posting: PostingCfg = Field(default_factory=PostingCfg, title="Posting")
    accounts: AccountsCfg = Field(default_factory=AccountsCfg, title="Accounts")
    copywriter: CopyCfg = Field(default_factory=CopyCfg, title="Copywriter")
    clip_import: ClipImportCfg = Field(default_factory=ClipImportCfg, title="Popular clips")
    analytics: AnalyticsCfg = Field(default_factory=AnalyticsCfg, title="Analytics")
    dashboard: DashboardCfg = Field(default_factory=DashboardCfg, title="Dashboard")
    pets: PetsCfg = Field(default_factory=PetsCfg, title="Desktop pet")
    studio: StudioCfg = Field(default_factory=StudioCfg, title="Studio")
    safety: SafetyCfg = Field(default_factory=SafetyCfg, title="Safety")
    viewer: ViewerCfg = Field(default_factory=ViewerCfg, title="Live viewer")
    updates: UpdatesCfg = Field(default_factory=UpdatesCfg, title="Updates")

    @model_validator(mode="after")
    def _clip_lengths(self) -> "Settings":
        if self.clip.min_len_s > self.clip.max_len_s:
            raise ValueError("clip.min_len_s must not exceed clip.max_len_s")
        if self.ai.hashtags_min > self.ai.hashtags_max:
            raise ValueError("ai.hashtags_min must not exceed ai.hashtags_max")
        return self


# --------------------------------------------------------------------------- metadata
def _walk_fields(model: type[BaseModel], prefix: tuple[str, ...] = ()):
    """Yield (path tuple, FieldInfo) for every leaf field (nested sections recursed)."""
    for name, info in model.model_fields.items():
        ann = info.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            yield from _walk_fields(ann, prefix + (name,))
        else:
            yield prefix + (name,), info


def _extra(info) -> dict:
    return info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}


LEAF_FIELDS: tuple[tuple[str, ...], ...] = tuple(p for p, _ in _walk_fields(Settings))
SECRET_FIELDS: frozenset[tuple[str, ...]] = frozenset(
    p for p, i in _walk_fields(Settings) if _extra(i).get("secret"))
RESTART_FIELDS: frozenset[tuple[str, ...]] = frozenset(
    p for p, i in _walk_fields(Settings) if _extra(i).get("restart"))


def get_path(data: dict, path: tuple[str, ...]) -> Any:
    for key in path:
        data = data[key]
    return data


def set_path(data: dict, path: tuple[str, ...], value: Any) -> None:
    for key in path[:-1]:
        data = data.setdefault(key, {})
    data[path[-1]] = value


def masked_dump(settings: Settings) -> dict:
    """Settings as JSON with every non-empty secret replaced by MASK."""
    data = settings.model_dump(mode="json")
    for path in SECRET_FIELDS:
        if get_path(data, path):
            set_path(data, path, MASK)
    return data


def merge_incoming(current: Settings, incoming: dict) -> Settings:
    """Validate an update from the UI. Secrets equal to MASK keep their stored value;
    sections/fields missing from ``incoming`` keep their current value."""
    merged = current.model_dump(mode="json")

    def deep(dst: dict, src: dict) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict) and not _is_map_field(dst, k):
                deep(dst[k], v)
            else:
                dst[k] = v

    deep(merged, incoming)
    for path in SECRET_FIELDS:
        try:
            if get_path(merged, path) == MASK:
                set_path(merged, path, get_path(current.model_dump(mode="json"), path))
        except (KeyError, TypeError):
            continue
    return Settings.model_validate(merged)


_MAP_FIELDS = {p[-1] for p, i in _walk_fields(Settings) if _extra(i).get("widget") == "map"}


def _is_map_field(parent: dict, key: str) -> bool:
    # map fields (keywords, facecam) are replaced wholesale so deleted keys disappear
    return key in _MAP_FIELDS


def changed_paths(old: Settings, new: Settings) -> list[tuple[str, ...]]:
    a, b = old.model_dump(mode="json"), new.model_dump(mode="json")
    return [p for p in LEAF_FIELDS if get_path(a, p) != get_path(b, p)]


def export_settings(settings: Settings, include_secrets: bool) -> dict:
    data = settings.model_dump(mode="json")
    if not include_secrets:
        for path in SECRET_FIELDS:
            set_path(data, path, "")
    return data


def import_settings(current: Settings, data: dict) -> Settings:
    """Import an exported file; blank secrets in the file keep the stored ones."""
    cleaned = json.loads(json.dumps(data))
    for path in SECRET_FIELDS:
        try:
            if not get_path(cleaned, path):
                set_path(cleaned, path, MASK)
        except (KeyError, TypeError):
            continue
    return merge_incoming(current, cleaned)


def reset_section(current: Settings, section: str) -> Settings:
    if section not in Settings.model_fields:
        raise KeyError(section)
    data = current.model_dump(mode="json")
    data[section] = Settings.model_fields[section].default_factory().model_dump(mode="json")
    return Settings.model_validate(data)


# --------------------------------------------------------------------------- persistence
class ConfigError(RuntimeError):
    """Raised when settings on disk cannot be read or validated."""


def atomic_write(path: Path, text: str) -> None:
    """Write text to path via a temp file + replace so readers never see partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------- prompt upgrades
# Prompts are stored in settings.json, so improving a default would never reach an existing
# install. Each entry is the sha256 of a prompt we shipped before; a stored prompt that still
# matches one of them was never edited by hand, so it is safe to replace with the current text.
PROMPT_FIELDS = {
    ("ai", "system_prompt"): (DEFAULT_SYSTEM_PROMPT, {
        "b064e5f6f207c8044b5b5c73298586fb9bdf375bec543fd31723ef31e28d38bb",
    }),
}


def upgrade_prompts(data: dict) -> bool:
    """Replace untouched older prompts in a loaded settings dict. True if anything changed."""
    changed = False
    for field, (current, old_hashes) in PROMPT_FIELDS.items():
        try:
            stored = get_path(data, field)
        except (KeyError, TypeError):
            continue
        if not isinstance(stored, str) or stored == current:
            continue
        if hashlib.sha256(stored.encode("utf-8")).hexdigest() in old_hashes:
            set_path(data, field, current)
            changed = True
    return changed


def load_settings(path: Path | None = None) -> Settings:
    """Load settings, creating the file with defaults on first run.

    Unknown keys are dropped and new fields get defaults, so old files keep working."""
    path = path or settings_path()
    if not path.exists():
        settings = Settings()
        save_settings(settings, path)
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for secret in SECRET_FIELDS:
            try:
                value = get_path(data, secret)
            except (KeyError, TypeError):
                continue
            if isinstance(value, str) and value.startswith(secure.PREFIX):
                set_path(data, secret, secure.unprotect(value))
        if upgrade_prompts(data):
            logger.info("settings: prompt upgraded to the current default")
        return Settings.model_validate(data)
    except secure.SecureError as exc:
        raise ConfigError(f"settings file {path} has keys encrypted for another Windows account: "
                          f"{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"settings file {path} is not valid JSON: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"settings file {path} failed validation: {exc}") from exc


def save_settings(settings: Settings, path: Path | None = None) -> None:
    data = settings.model_dump(mode="json")
    if settings.app.encrypt_secrets and secure.available():
        for secret in SECRET_FIELDS:
            set_path(data, secret, secure.protect(get_path(data, secret)))
    atomic_write(path or settings_path(), json.dumps(data, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- resolved paths
@dataclass(frozen=True)
class AppPaths:
    data: Path
    buffer: Path
    work: Path
    clips: Path
    logs: Path
    db: Path
    tokens: Path
    models: Path | None = None

    def ensure(self) -> None:
        for p in (self.data, self.buffer, self.work, self.clips, self.logs, self.models):
            if p is None:
                continue
            p.mkdir(parents=True, exist_ok=True)


def resolve_paths(settings: Settings) -> AppPaths:
    data = Path(os.path.expandvars(settings.app.data_dir)) if settings.app.data_dir else home_dir()

    def sub(value: str, name: str) -> Path:
        return Path(os.path.expandvars(value)) if value else data / name

    return AppPaths(data=data, buffer=sub(settings.app.buffer_dir, "buffer"),
                    work=sub(settings.app.work_dir, "work"), clips=sub(settings.app.clips_dir, "clips"),
                    logs=sub(settings.app.logs_dir, "logs"), db=data / "clipbot.db",
                    tokens=data / "tokens.json",
                    models=Path(os.path.expandvars(settings.app.models_dir)) if settings.app.models_dir else None)


def _which(configured: str, name: str) -> str | None:
    if configured:
        p = Path(os.path.expandvars(configured))
        return str(p) if p.exists() else shutil.which(configured)
    return shutil.which(name)


def ffmpeg_exe(settings: Settings) -> str | None:
    return _which(settings.paths.ffmpeg, "ffmpeg")


def ffprobe_exe(settings: Settings) -> str | None:
    return _which(settings.paths.ffprobe, "ffprobe")


def streamlink_cmd(settings: Settings) -> list[str]:
    """Command prefix that runs streamlink as a subprocess."""
    if settings.paths.streamlink:
        exe = _which(settings.paths.streamlink, "streamlink")
        return [exe or settings.paths.streamlink]
    if is_frozen():
        return [sys.executable, "--streamlink"]
    return [sys.executable, "-m", "streamlink"]


# --------------------------------------------------------------------------- tokens
def load_tokens(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(secure.unprotect(path.read_text(encoding="utf-8").strip()))
    except secure.SecureError as exc:
        raise ConfigError(f"token file {path} was encrypted by another Windows account") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"token file {path} is corrupt: {exc}") from exc


def save_tokens(path: Path, tokens: dict, encrypt: bool = True) -> None:
    text = json.dumps(tokens, indent=2)
    atomic_write(path, secure.protect(text) if encrypt else text)

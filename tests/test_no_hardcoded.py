"""Guard against tunables creeping into code instead of Settings.

1. No anonymous numeric literals. Allowed: identity/unit values (0, ±1, 2, 24, 60, 100, 1000,
   1024, 3600, 86400 — seconds/minutes/hours/percent/bytes), HTTP status codes compared
   against ``.status_code``, ``round()`` digit counts, and module-level UPPER_CASE protocol
   constants that are reviewed below (API limits, Win32 ids, …).
2. No module contains a default string of a free-text Settings field (model name, preset,
   URL, prompt, template, colour, font…) — those must be read from Settings.
3. Every Settings field is actually read somewhere (no dead settings), and the detector,
   capture, editor, scheduler and pipeline read their own sections' fields.
"""
import ast
import re
from pathlib import Path
from typing import get_args, get_origin, Literal

import pytest

from clipbot import config as C

PKG = Path(__file__).resolve().parent.parent / "clipbot"
UNIT_VALUES = {0, 1, -1, 2, 24, 60, 100, 1000, 1024, 3600, 86400}
# Drawing proportions of the icon are artwork; hardware.py IS the table of recommended values.
EXCLUDED = {"config.py", "icon.py", "hardware.py"}
REVIEWED_CONSTANTS = {
    "util.py": {"ERR_SNIPPET"},                                         # log/display truncation
    "app.py": {"STARTUP_POLL_S", "LAN_PROBE_PORT"},                                       # wait-for-bind poll
    "discovery.py": {"TOKEN_REFRESH_MARGIN_S", "KICK_CHANNELS_MAX",      # OAuth margin, API limits
                     "KICK_V1_SEARCH_MAX", "KICK_V1_GONE", "KEYS_REJECTED"},
    "chat.py": {"TWITCH_NICK_DIGITS"},
    "agent/skills.py": {"NAME_MAX", "DESC_MAX"},                        # file-name and field caps
    "agent/tools.py": {"CLIPS_MAX", "LOG_MAX", "TEXT_MAX", "CLIPS_DEFAULT",
                       "CHAT_SAMPLE_MAX", "LOG_DEFAULT", "LOG_BYTES"},             # how much is shown at once
    "agent/mcpclient.py": {"PROTOCOL", "START_TIMEOUT_S", "CALL_TIMEOUT_S", "LINE_MAX",
                           "SUMMARY_MAX", "STOP_GRACE_S", "DESC_MAX"},
    "sweeper.py": {"GB", "HOUR_S"},                                     # unit conversions
    "storage.py": {"GB"},                                  # justinfan##### format
    "editor.py": {"_WATERMARK_ALIGN", "_CAPTION_ALIGN", "_ALPHA_MAX", "_BOX_FIELDS",
                  "_HOOK_ALIGN", "_HOOK_PAD_DIV", "_HOOK_FADE_IN_MS", "_HOOK_FADE_OUT_MS"},  # ASS format values
    "clipability.py": {"SCORE_MAX", "PEAK_DEFAULT_FRACTION"},                                    # the 0-10 scale
    "scheduler.py": {"DAY_S"},
    "analytics.py": {"DAY_S"},
    "copywriter.py": {"YOUTUBE_TITLE_MAX", "YOUTUBE_DESC_MAX", "YOUTUBE_TAGS_TOTAL_MAX",
                      "CAPTION_MAX", "FACEBOOK_TITLE_MAX", "DISCORD_MAX"},   # API limits
    "pipeline.py": {"GIB", "SWEEP_IDLE_S", "RAW_SUFFIX"},
    "db.py": {"SQLITE_BUSY_TIMEOUT_S"},
    "vision.py": {"GROUND_SCALE"},                                      # Qwen-VL box coordinates
    "models.py": {"ID_LEN"},
    "auth.py": {"PBKDF2_ITERATIONS", "SALT_BYTES", "TOKEN_BYTES", "PROFILE_ID_BYTES",
                "LAST_SEEN_WRITE_S"},
    "desktop.py": {"WS_EX_LAYERED", "WS_EX_TRANSPARENT", "LWA_COLORKEY", "LWA_ALPHA", "LAYER_OPAQUE", "VK_LBUTTON",
                   "KEY_DOWN", "MS", "GREEN_SHIFT", "BLUE_SHIFT", "CARD_HOVER_MS",
                   "ERROR_ALREADY_EXISTS", "DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP",
                   "CREATE_BREAKAWAY_FROM_JOB", "TRAY_TIP_MAX", "ENGINE_WAIT_FACTOR",
                   "GWL_EXSTYLE", "WS_EX_TOOLWINDOW", "TASKBAR_SPAN", "SM_CYSCREEN",
                   "WS_EX_APPWINDOW", "SPI_GETWORKAREA", "FALLBACK_AREA", "RECT_LONGS", "SWP_NOSIZE", "SWP_NOMOVE",
                   "SWP_NOZORDER", "SWP_NOACTIVATE", "PET_KEY_ARGB", "WM_SETICON", "IMAGE_ICON", "LR_LOADFROMFILE", "ICON_SIZES"},
    "pets.py": {"HOUR_S", "GRAPH_HOURS", "TOP_STREAMS", "RECENT_MAX"},   # card layout sizes
    "aiplan.py": {"GIB_PER_MIB"},
    "updater.py": {"DETACHED_PROCESS", "CHUNK"},
    "syslog.py": {"HOUR_S", "DAY_HOURS", "GROUP_KEY_MAX", "TOP_GROUPS", "TOP_REASONS"},
    "__main__.py": {"STD_OUTPUT_HANDLE", "STD_ERROR_HANDLE"},
    "publishers/oauth.py": {"REFRESH_MARGIN_S"},
    "publishers/youtube.py": {"TITLE_MAX", "DESCRIPTION_MAX", "IDS_PER_CALL"},
    "publishers/tiktok.py": {"CAPTION_MAX", "MIN_CHUNK", "MAX_CHUNK", "IDS_PER_CALL"},
    "publishers/instagram.py": {"CAPTION_MAX", "DAY_S"},
    "publishers/discord.py": {"CONTENT_MAX"},
}
# Win32 API flag values used inside functions (job objects, message boxes) are OS constants.
WIN32_LOCALS = {"JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE", "JOB_OBJECT_LIMIT_BREAKAWAY_OK",
                "JobObjectExtendedLimitInformation", "PROCESS_SET_QUOTA_TERMINATE",
                "MB_ICONWARNING"}


def modules():
    for p in sorted(PKG.rglob("*.py")):
        rel = p.relative_to(PKG).as_posix()
        if p.name not in EXCLUDED and "__pycache__" not in rel:
            yield rel, p


def _parents(tree):
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node


def _allowed(node: ast.Constant, rel: str) -> bool:
    v = node.value
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return True
    if v in UNIT_VALUES:
        return True
    p = getattr(node, "parent", None)
    if isinstance(p, ast.UnaryOp):
        p = getattr(p, "parent", None)
    # HTTP status codes: r.status_code == 200 / in (204, 404) / >= 500
    cmp = p if isinstance(p, ast.Compare) else getattr(p, "parent", None)
    if isinstance(cmp, ast.Compare):
        if any(isinstance(x, ast.Attribute) and x.attr == "status_code"
               for x in [cmp.left, *cmp.comparators]):
            return True
    # HTTPException(404) / JSONResponse(..., status_code=422)
    if isinstance(p, ast.keyword) and p.arg == "status_code":
        return True
    if isinstance(p, ast.Call) and getattr(p.func, "id", None) == "HTTPException":
        return True
    # round(x, 3)
    if isinstance(p, ast.Call) and getattr(p.func, "id", None) == "round" and node in p.args[1:]:
        return True
    # reviewed module-level constants and Win32 flag names
    anc = p
    while anc is not None:
        if isinstance(anc, ast.Assign):
            names = {t.id for t in anc.targets if isinstance(t, ast.Name)}
            if names & (REVIEWED_CONSTANTS.get(rel, set()) | WIN32_LOCALS):
                return True
        anc = getattr(anc, "parent", None)
    return False


@pytest.mark.parametrize("rel,path", list(modules()))
def test_no_magic_numbers(rel, path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    _parents(tree)
    bad = [f"{rel}:{n.lineno} {n.value!r}" for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and not _allowed(n, rel)]
    assert not bad, "numeric literals that belong in Settings:\n" + "\n".join(bad)


def _free_text_defaults() -> dict[str, str]:
    out = {}
    for path, info in C._walk_fields(C.Settings):
        if get_origin(info.annotation) is Literal:
            continue           # enum choices (codec names, privacy levels) are protocol values
        d = info.get_default(call_default_factory=True)
        if isinstance(d, str) and len(d) >= 3:
            out[".".join(path)] = d
    return out


def test_no_setting_defaults_hardcoded():
    defaults = _free_text_defaults()
    assert "ai.model" in defaults and "edit.preset" in defaults
    hits = []
    for rel, path in modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                  and isinstance(n.value, str)}
        for field, value in defaults.items():
            if value in consts:
                hits.append(f"{rel} contains the default of {field}: {value!r}")
    assert not hits, "\n".join(hits)


def _code_without_config_models() -> str:
    parts = []
    for p in PKG.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if p.name == "config.py":
            tree = ast.parse(text)
            text = "\n".join(ast.get_source_segment(text, n) or "" for n in tree.body
                             if not isinstance(n, ast.ClassDef))
        parts.append(text)
    for page in (PKG / "dashboard" / "static").glob("*.html"):   # the pet page reads pets.*
        parts.append(page.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_every_setting_is_read_somewhere():
    code = _code_without_config_models()
    unused = [".".join(p) for p in C.LEAF_FIELDS
              if not re.search(r"\.\s*" + re.escape(p[-1]) + r"\b", code)
              and f'"{p[-1]}"' not in code]
    assert not unused, f"settings nobody reads: {unused}"


SECTION_OWNERS = {
    "detector": ("detector", ["detector.py", "pipeline.py"]),
    "capture": ("capture", ["capture.py", "assembler.py", "pipeline.py", "editor.py", "checklist.py", "media.py"]),
    "edit": ("edit", ["editor.py", "assembler.py", "pipeline.py", "vision.py"]),
    "posting": ("posting", ["scheduler.py", "publishers/base.py", "publishers/youtube.py",
                            "publishers/tiktok.py", "publishers/instagram.py",
                            "publishers/discord.py"]),
    "backlog": ("backlog", ["pipeline.py"]),
    "clip": ("clip", ["assembler.py", "pipeline.py", "clipability.py", "capture.py", "media.py", "scheduler.py", "clip_import.py"]),
}


@pytest.mark.parametrize("section", list(SECTION_OWNERS))
def test_core_modules_read_their_settings(section):
    sec, files = SECTION_OWNERS[section]
    code = "\n".join((PKG / f).read_text(encoding="utf-8") for f in files)
    model = C.Settings.model_fields[sec].annotation
    missing = []
    for path, _ in C._walk_fields(model):
        name = path[-1]
        if not re.search(r"\.\s*" + re.escape(name) + r"\b", code):
            missing.append(".".join((sec, *path)))
    assert not missing, f"{section}: fields not read by {files}: {missing}"


def test_guard_catches_a_planted_magic_number():
    """The scanner itself must flag a tunable literal (otherwise the guard proves nothing)."""
    tree = ast.parse("async def tick():\n    await asyncio.sleep(7.5)\nWINDOW = 42\n")
    _parents(tree)
    flagged = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and not _allowed(n, "detector.py")]
    assert sorted(flagged) == [7.5, 42]

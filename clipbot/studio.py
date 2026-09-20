"""Studio: restyle one clip on the fly (captions, hook, motion, layout, trim) and save looks
as themes.

A style is a dict of ``edit.*`` overrides limited to ``STYLE_FIELDS``. It is validated by
merging it into the real Settings, so a bad value fails exactly like it would in Settings.
Built-in themes are original presets; user themes live in <data>/themes.json.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import STUDIO_THEMES, Settings, atomic_write, merge_incoming

logger = logging.getLogger("clipbot.studio")

# edit.* fields the Studio may change (everything visual; never paths, codecs or timeouts)
STYLE_FIELDS = (
    "layout", "background_brightness", "blur_sigma", "sharpen",
    "captions", "font", "font_size", "text_color", "highlight_color", "outline_color", "outline",
    "shadow", "words_per_line", "caption_y_pct", "caption_uppercase", "caption_pop",
    "caption_pop_pct", "hook_text", "hook_seconds", "hook_font_size", "hook_y_pct",
    "hook_box_color", "hook_box_opacity", "punch_zoom", "punch_zoom_amount", "progress_bar",
    "progress_bar_height", "progress_bar_color", "watermark_position", "watermark_opacity",
    "watermark_size",
)

BUILTIN_THEMES = STUDIO_THEMES          # original looks, defined with the other defaults


def clean_style(style: dict) -> dict:
    """Keep only Studio fields (unknown keys are dropped, not errors)."""
    return {k: v for k, v in (style or {}).items() if k in STYLE_FIELDS}


def apply_style(settings: Settings, style: dict) -> Settings:
    """Settings with ``style`` applied to edit.*; raises pydantic ValidationError if invalid."""
    return merge_incoming(settings, {"edit": clean_style(style)})


def current_style(settings: Settings) -> dict:
    e = settings.edit
    return {k: getattr(e, k) for k in STYLE_FIELDS}


class ThemeStore:
    """User themes in <data>/themes.json: {name: style}."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("themes file unreadable (%s); ignoring it", exc)
            return {}
        return {str(k): clean_style(v) for k, v in data.items() if isinstance(v, dict)}

    def all(self) -> dict[str, dict]:
        return {"builtin": BUILTIN_THEMES, "mine": self.load()}

    def save(self, name: str, style: dict) -> None:
        name = name.strip()
        if not name or name in BUILTIN_THEMES:
            raise ValueError("pick a new theme name (built-in names are taken)")
        data = self.load()
        data[name] = clean_style(style)
        atomic_write(self.path, json.dumps(data, indent=1))

    def delete(self, name: str) -> bool:
        data = self.load()
        if name not in data:
            return False
        del data[name]
        atomic_write(self.path, json.dumps(data, indent=1))
        return True

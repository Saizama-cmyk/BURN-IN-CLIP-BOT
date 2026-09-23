"""The Ashvane mark, drawn with Pillow for tiny icon sizes.

Two satin-metal vanes lean into an A and an ember slash cuts through them. The Blender master
is used for larger icons; this flat companion keeps the same silhouette at 16 to 32 pixels.

``python -m clipbot.icon [folder]`` writes clipbot.ico (16–256 px) and clipbot.png.
"""
from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

logger = logging.getLogger("clipbot.icon")

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
MASTER = 1024
_FRAME = 2.05                 # the Blender camera's orthographic width, in mark units
_SLASH_TILT = math.radians(11)


def make_icon(size: int = MASTER) -> Image.Image:
    """A flat counterpart to the Blender mark; legible in the Windows tray.

    Same outlines as art/ashvane_scene.py (two leaning vanes and the ember slash), mapped the
    way that scene's camera frames them, so the small and large icons line up exactly."""
    s = MASTER
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    silver, ember = (243, 240, 233, 255), (240, 104, 52, 255)

    def px(x: float, z: float) -> tuple[float, float]:
        return ((x / _FRAME + .5) * s, (.5 - z / _FRAME) * s)

    left = [(-.80, -.80), (-.50, -.80), (-.035, .80), (-.20, .80)]
    right = [(-x, z) for x, z in reversed(left)]
    for vane in (left, right):
        d.polygon([px(*pt) for pt in vane], fill=silver)
    c, sn = math.cos(_SLASH_TILT), math.sin(_SLASH_TILT)
    slash = [(-.74, -.085), (.48, -.085), (.80, 0.), (.48, .085), (-.74, .085)]
    d.polygon([px(x * c - z * sn, x * sn + z * c - .17) for x, z in slash], fill=ember)
    return out if size == s else out.resize((size, size), Image.Resampling.LANCZOS)


RENDER_NAME = "clipbot-3d.png"   # Blender master from art/build_art.py (optional)
SMALL_MAX = 32                   # at and below this, the flat vector mark reads crisper


def write_assets(folder: Path) -> Path:
    """clipbot.ico + clipbot.png. Uses the 3D-rendered master for large sizes when present."""
    folder.mkdir(parents=True, exist_ok=True)
    flat = make_icon()
    render_path = folder / RENDER_NAME
    big = Image.open(render_path).convert("RGBA") if render_path.exists() else flat
    frames = [(flat if w <= SMALL_MAX else big).resize((w, h), Image.LANCZOS) for w, h in ICO_SIZES]
    ico = folder / "clipbot.ico"
    frames[-1].save(ico, format="ICO", sizes=ICO_SIZES, append_images=frames[:-1])
    big.resize((256, 256), Image.LANCZOS).save(folder / "clipbot.png")
    return ico


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "assets"
    logger.info("wrote %s", write_assets(root))

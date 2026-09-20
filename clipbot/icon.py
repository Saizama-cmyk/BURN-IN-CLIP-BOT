"""The BURN-IN edit-bracket mark, drawn with Pillow for tiny icon sizes.

Two warm metal edit brackets enclose a single ember playhead. The Blender master is used
for larger icons; this flat companion keeps the same silhouette at 16 to 32 pixels.

``python -m clipbot.icon [folder]`` writes clipbot.ico (16–256 px) and clipbot.png.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

logger = logging.getLogger("clipbot.icon")

TILE_TOP = (22, 34, 56)       # obsidian navy, lighter at the top edge
TILE_BOTTOM = (6, 11, 20)
RIM = (58, 84, 124)           # bevel highlight
GRID = (30, 48, 76)
AMBER = (242, 169, 59)
AMBER_HOT = (255, 214, 140)
CYAN = (79, 195, 217)
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
MASTER = 1024

# signal trace in unit coordinates: calm baseline, one sharp spike, settle
_TRACE = [(0.17, 0.66), (0.29, 0.62), (0.38, 0.65), (0.46, 0.63), (0.53, 0.24), (0.60, 0.72),
          (0.67, 0.58), (0.75, 0.61), (0.83, 0.60)]
_PEAK = 4


def _tile_mask(s: int, radius: int) -> Image.Image:
    m = Image.new("L", (s, s), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=255)
    return m


def make_icon(size: int = MASTER) -> Image.Image:
    """A flat counterpart to the Blender edit brackets; legible in the Windows tray."""
    s = MASTER
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    # This is the same [ | ] silhouette as art/burnin_scene.py.
    silver, ember = (243, 240, 233, 255), (255, 137, 79, 255)
    def rect(bounds, color):
        d.rounded_rectangle(tuple(int(v * s) for v in bounds), radius=int(s * .020), fill=color)
    for x0, x1, arm0, arm1 in ((.12,.235,.12,.405),(.765,.88,.595,.88)):
        rect((x0,.12,x1,.88),silver)
        rect((arm0,.12,arm1,.235),silver)
        rect((arm0,.765,arm1,.88),silver)
    rect((.455,.175,.545,.825),ember)
    return out if size == s else out.resize((size,size),Image.Resampling.LANCZOS)


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

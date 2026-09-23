"""Build-time assets for the BURN-IN installer and exe (run with the project venv).

    python installer/make_assets.py
Writes installer/version_info.txt (PyInstaller --version-file: company, product, version shown
in Explorer → Properties and the Apps list) and the Inno Setup wizard images, composed from the
Blender art (art/out) in the chrome style.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from clipbot import __version__  # noqa: E402
from clipbot.config import COMPANY, PRODUCT  # noqa: E402
from clipbot.updater import _source_error  # noqa: E402

logger = logging.getLogger("clipbot.installer")
HERE = Path(__file__).resolve().parent
STATIC = ROOT / "clipbot" / "dashboard" / "static" / "art"


def version_file() -> None:
    parts = [int(x) for x in __version__.split(".")] + [0] * 4
    v = tuple(parts[:4])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', '{COMPANY}'),
    StringStruct('FileDescription', '{PRODUCT}'),
    StringStruct('FileVersion', '{__version__}'),
    StringStruct('InternalName', 'BurnIn'),
    StringStruct('LegalCopyright', '(c) {COMPANY}'),
    StringStruct('OriginalFilename', 'BurnIn.exe'),
    StringStruct('ProductName', '{PRODUCT}'),
    StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""
    (HERE / "version_info.txt").write_text(text, encoding="utf-8")
    (HERE / "version.txt").write_text(__version__, encoding="utf-8")      # read by build_installer.bat


def font(size: int) -> ImageFont.ImageFont:
    for name in ("seguibl.ttf", "segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def chrome_text(img: Image.Image, xy, text: str, size: int) -> None:
    """Text filled with a vertical chrome gradient, with a dark drop."""
    f = font(size)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).text(xy, text, font=f, fill=255)
    grad = Image.new("RGB", img.size)
    top, h = xy[1], size
    for y in range(img.height):
        t = min(1, max(0, (y - top) / max(1, h)))
        stops = [(0, 255), (0.42, 214), (0.52, 130), (0.7, 205), (1, 244)]
        for (a, va), (b, vb) in zip(stops, stops[1:]):
            if a <= t <= b:
                v = int(va + (vb - va) * (t - a) / (b - a))
                break
        ImageDraw.Draw(grad).line([(0, y), (img.width, y)], fill=(v, v, min(255, v + 6)))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((xy[0], xy[1] + 2), text, font=f, fill=(0, 0, 0, 200))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(2)))
    img.paste(grad, (0, 0), mask)


def wizard_images() -> None:
    bg = Image.open(STATIC / "bg.jpg").convert("RGBA")
    mark = Image.open(STATIC / "mark.png").convert("RGBA")
    # big side panel 164x314 (Inno scales for DPI; supply 2x)
    side = bg.resize((int(bg.width * 628 / bg.height), 628), Image.LANCZOS).crop((380, 0, 708, 628))
    side = Image.alpha_composite(side, Image.new("RGBA", side.size, (5, 5, 7, 150)))
    m = mark.resize((200, 200), Image.LANCZOS)
    side.alpha_composite(m, ((side.width - 200) // 2, 150))
    chrome_text(side, (24, 372), PRODUCT, 60)
    ImageDraw.Draw(side).text((28, 460), "YOUR STREAM. REPLAYED.", font=font(17), fill=(170, 174, 182))
    side.convert("RGB").save(HERE / "wizard.bmp")
    small = Image.new("RGBA", (110, 110), (10, 10, 12, 255))
    small.alpha_composite(mark.resize((104, 104), Image.LANCZOS), (3, 3))
    small.convert("RGB").save(HERE / "wizard_small.bmp")


PLATE = (7, 7, 8, 255)     # the brand's near-black; the mark's own plate sits on this
ADAPTIVE_SAFE = 0.66       # Android masks an adaptive icon to about this much of the canvas
MASKABLE_SAFE = 0.60       # a maskable web icon may be cropped to a circle
PHONE_PX = 1024            # what the stores want; everything else is derived from it
WEB_PX = 512


def _on_plate(mark: Image.Image, px: int, fill: float) -> Image.Image:
    """The mark centred on an opaque plate, filling ``fill`` of the canvas.

    Opaque matters: iOS refuses alpha and composites it to black, and Android and the web mask
    the icon to their own shape. Painting the brand colour ourselves is what makes the phone icon
    the same icon as the one on Windows instead of a lookalike on a black square."""
    out = Image.new("RGBA", (px, px), PLATE)
    side = int(px * fill)
    out.alpha_composite(mark.resize((side, side), Image.LANCZOS), ((px - side) // 2,) * 2)
    return out


def phone_icons() -> None:
    """Every phone and web icon, rendered from the one mark the desktop app already uses."""
    mark = Image.open(STATIC / "mark.png").convert("RGBA")
    phone = ROOT / "phone" / "assets"
    phone.mkdir(parents=True, exist_ok=True)

    # iOS: square, opaque, no rounding of our own - iOS applies its own mask.
    _on_plate(mark, PHONE_PX, 1.0).convert("RGB").save(phone / "icon-ios.png")

    # Android adaptive: the foreground is transparent and must keep clear of the mask's edge,
    # so the mark is inset; the launcher paints `backgroundColor` behind it.
    fg = Image.new("RGBA", (PHONE_PX, PHONE_PX), (0, 0, 0, 0))
    side = int(PHONE_PX * ADAPTIVE_SAFE)
    fg.alpha_composite(mark.resize((side, side), Image.LANCZOS), ((PHONE_PX - side) // 2,) * 2)
    fg.save(phone / "icon-adaptive.png")

    # Notifications: Android draws these as a silhouette, so shape is all that survives.
    fg.resize((256, 256), Image.LANCZOS).save(phone / "icon-notification.png")

    # Web / installed-to-home-screen, including a maskable one that may be cropped to a circle.
    _on_plate(mark, WEB_PX, 1.0).save(STATIC / "mark-web.png")
    _on_plate(mark, WEB_PX, MASKABLE_SAFE).save(STATIC / "mark-maskable.png")
    logger.info("phone and web icons rendered from mark.png")


def legal_copies() -> None:
    """The app shows the same LICENSE/NOTICE/TERMS/PRIVACY that ship in the repo."""
    dst = ROOT / "clipbot" / "dashboard" / "static" / "legal"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "NOTICE.md", "TERMS.md", "PRIVACY.md"):
        (dst / name).write_bytes((ROOT / name).read_bytes())


DEFAULT_REPO = "Saizama-cmyk/BURN-IN-CLIP-BOT"   # where builds are published; CLIPBOT_REPO wins


def release_config() -> None:
    """Bake the update source into the build, so a fresh install updates itself with no setup."""
    repo = (os.environ.get("CLIPBOT_REPO", "").strip() or DEFAULT_REPO)
    if repo:
        error = _source_error(repo, "BURN-IN-Setup.exe")
        if error:
            raise ValueError(error)
    (ROOT / "assets").mkdir(exist_ok=True)
    (ROOT / "assets" / "release_config.json").write_text(
        json.dumps({"repo": repo}, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    release_config()
    legal_copies()
    version_file()
    wizard_images()
    phone_icons()
    logger.info("installer assets ready (version %s)", __version__)


if __name__ == "__main__":
    main()

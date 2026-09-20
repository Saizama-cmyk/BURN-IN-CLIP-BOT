"""Pack Blender pet frames (art/out_pets/<species>/<action>/NNN.png) into one horizontal
sprite strip per action for the pet window, plus a manifest.

    python art/build_pets.py
Writes clipbot/dashboard/static/pets/<species>/<action>.png and pets/manifest.json.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger("clipbot.art")
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "art" / "out_pets"
DST = ROOT / "clipbot" / "dashboard" / "static" / "pets"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    manifest: dict = {}
    for sp_dir in sorted(p for p in SRC.iterdir() if p.is_dir()):
        acts = {}
        for act_dir in sorted(p for p in sp_dir.iterdir() if p.is_dir()):
            frames = [Image.open(f).convert("RGBA") for f in sorted(act_dir.glob("*.png"))]
            if not frames:
                continue
            w, h = frames[0].size
            strip = Image.new("RGBA", (w * len(frames), h))
            for i, im in enumerate(frames):
                strip.paste(im, (i * w, 0))
            out = DST / sp_dir.name / f"{act_dir.name}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            strip.save(out, optimize=True)
            acts[act_dir.name] = len(frames)
        if acts:
            manifest[sp_dir.name] = {"frame": w, "actions": acts}
            logger.info("%s: %d actions", sp_dir.name, len(acts))
    (DST / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    icons = ROOT / "art" / "out_icons"                  # 3D moodlet icons, one set per finish
    for f in icons.glob("*/*.png"):
        out = DST / "icons" / f.parent.name / f.name
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.open(f).save(out, optimize=True)
    logger.info("icons: %d", len(list((DST / "icons").glob("*/*.png"))))


if __name__ == "__main__":
    main()

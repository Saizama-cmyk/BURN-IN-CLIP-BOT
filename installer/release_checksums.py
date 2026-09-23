"""Write SHA-256 checksums for release assets without loading them into memory."""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def write_checksums(dist: Path) -> Path:
    assets = [dist / "Ashvane-Setup.exe"]
    web = dist / "Ashvane-WebSetup.exe"
    if web.is_file():
        assets.append(web)
    lines = []
    for asset in assets:
        with asset.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        lines.append(f"{digest}  {asset.name}")
    output = dist / "SHA256SUMS.txt"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    print(write_checksums(ROOT / "dist"))


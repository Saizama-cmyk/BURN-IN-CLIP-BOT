"""Write the sideload feed so the phone app updates itself, whatever tool installed it.

Every sideloader that can auto-update (AltStore, SideStore, Feather, Gbox and friends) reads the
same JSON "source" format: a list of apps and where each .ipa lives. Tools without a feed
(Sideloadly, ESign, TrollStore) use the unchanging phone-latest download URL instead - both point
at the same build. The PC can be Windows, macOS or Linux; this is only a file on the web.

    python installer/altstore_source.py <repo> <tag> <ipa-url> [size-bytes]

Writes docs/sideload.json plus docs/altstore.json (the name older tools look for).
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipbot import __version__                                        # noqa: E402
from clipbot.config import PRODUCT                                     # noqa: E402

OUT = ROOT / "docs" / "sideload.json"
ALIAS = ROOT / "docs" / "altstore.json"   # same feed, the name older tools expect
BUNDLE_ID = "os.spike.burnin.remote"
ICON = "https://raw.githubusercontent.com/{repo}/main/clipbot/dashboard/static/art/mark-96.png"
SCREENSHOT = "https://raw.githubusercontent.com/{repo}/main/clipbot/dashboard/static/art/mark.png"
TINT = "DDE1E7"
MIN_IOS = "15.1"


def source(repo: str, tag: str, ipa_url: str, size: int) -> dict:
    # tags look like "phone-v1.0.5"; sideloaders compare plain numbers, so hand them just that
    version = tag.split("-v")[-1].lstrip("vV") if tag else __version__
    if not version[:1].isdigit():
        version = __version__
    return {
        "name": f"{PRODUCT} Remote",
        "identifier": "os.spike.burnin",
        "subtitle": "Run your clip desk from your phone.",
        "iconURL": ICON.format(repo=repo),
        "website": f"https://github.com/{repo}",
        "tintColor": TINT,
        "apps": [{
            "name": f"{PRODUCT} Remote",
            "bundleIdentifier": BUNDLE_ID,
            "developerName": "BURN-IN",
            "subtitle": "Status, streams, clips and post-now, over your own Wi-Fi.",
            "localizedDescription": (
                f"The {PRODUCT} desk on your phone: what it is watching, what it has cut, and a "
                "post-now button. Talks to the app on your PC over your own network - no account, "
                "nothing leaves your Wi-Fi."
            ),
            "iconURL": ICON.format(repo=repo),
            "tintColor": TINT,
            "category": "utilities",
            "screenshotURLs": [SCREENSHOT.format(repo=repo)],
            "versions": [{
                "version": version,
                "date": date.today().isoformat(),
                "localizedDescription": f"{PRODUCT} {version}.",
                "downloadURL": ipa_url,
                "size": size,
                "minOSVersion": MIN_IOS,
            }],
        }],
        "news": [],
    }


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    repo, tag, ipa_url = sys.argv[1], sys.argv[2], sys.argv[3]
    size = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    feed = json.dumps(source(repo, tag, ipa_url, size), indent=2)
    OUT.write_text(feed, encoding="utf-8")
    ALIAS.write_text(feed, encoding="utf-8")
    print(f"wrote {OUT} and {ALIAS}")


def version_note(tag: str) -> str:
    return f"tag {tag}" if tag else "no tag"


if __name__ == "__main__":
    main()

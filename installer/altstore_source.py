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
PRODUCT = "Ashvane"          # kept in step with clipbot/config.py by the brand test

OUT = ROOT / "docs" / "sideload.json"
ALIAS = ROOT / "docs" / "altstore.json"   # same feed, the name older tools expect
BUNDLE_ID = "app.ashvane.mobile"
# full-size and opaque: sideloaders show this large, and iOS turns transparency black
ICON = "https://raw.githubusercontent.com/{repo}/main/phone/assets/icon-ios.png"
SCREENSHOT = "https://raw.githubusercontent.com/{repo}/main/docs/listing/screen-{n}.png"
SCREENSHOTS = 4                            # docs/listing/screen-1..4.png, from make_assets.py
TINT = "F06834"                            # the ember of the mark
DESCRIPTION = (
    f"{PRODUCT} watches the biggest Twitch and Kick streams on your PC, catches the moments chat "
    "loses it over, and turns them into vertical clips ready to post. This app puts the whole "
    "desk in your pocket, next to a private AI that lives on the phone.\n\n"
    "REMOTE\n"
    "- See every stream it is watching, with viewers and how hot chat is running\n"
    "- Look through the clips it made and post the good ones straight away\n"
    "- Change the look of your clips in Studio, and every setting, from the couch\n"
    "- Pause and resume capture, and read what it is doing and why\n\n"
    "ASSISTANT\n"
    "- Runs on the phone itself: it checks your phone and downloads the largest model that "
    "will run well on it, then works with no PC and no internet\n"
    "- Attach photos, videos and files; send a video link and it watches it with your PC's help\n"
    "- Skills you call with /name - research, learn, grill-me, watch-this, plan and more - "
    "plus your own, and rules that apply to every chat\n"
    "- A Code mode for writing and explaining code\n\n"
    "PRIVATE BY DESIGN\n"
    "Locked with Face ID, Touch ID or your passcode. No account and no cloud: the app talks "
    "only to your own PC, on your Wi-Fi or over Tailscale, and asks for your "
    f"{PRODUCT} password."
)
MIN_IOS = "15.1"


def app_version() -> str:
    """Whatever the phone app is stamped with right now."""
    return json.loads((ROOT / "phone" / "app.json").read_text(encoding="utf-8"))["expo"]["version"]


def source(repo: str, tag: str, ipa_url: str, size: int) -> dict:
    # tags look like "phone-v1.0.5"; sideloaders compare plain numbers, so hand them just that
    version = tag.split("-v")[-1].lstrip("vV") if tag else __version__
    if not version[:1].isdigit():
        version = app_version()
    return {
        "name": PRODUCT,
        "identifier": "app.ashvane",
        "subtitle": "Your clip desk and a private AI, in your pocket.",
        "iconURL": ICON.format(repo=repo),
        "website": f"https://github.com/{repo}",
        "tintColor": TINT,
        "apps": [{
            "name": PRODUCT,
            "bundleIdentifier": BUNDLE_ID,
            "developerName": PRODUCT,
            "subtitle": "Your clip desk and a private AI, in your pocket.",
            "localizedDescription": DESCRIPTION,
            "iconURL": ICON.format(repo=repo),
            "tintColor": TINT,
            "category": "utilities",
            "screenshotURLs": [SCREENSHOT.format(repo=repo, n=n)
                               for n in range(1, SCREENSHOTS + 1)],
            # The same release stated twice. Newer sideloaders read "versions"; older AltStore
            # builds read these flat fields and refuse the install with "version does not match"
            # when they are missing. Writing both means any tool can install it.
            "version": version,
            "versionDate": date.today().isoformat(),
            "versionDescription": f"{PRODUCT} {version}.",
            "downloadURL": ipa_url,
            "size": size,
            "minOSVersion": MIN_IOS,
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

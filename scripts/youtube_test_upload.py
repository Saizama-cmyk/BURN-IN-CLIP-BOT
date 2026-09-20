"""Real YouTube upload through ClipBot's own publisher, forced PRIVATE (nothing goes public).

    python scripts/youtube_test_upload.py [video.mp4]

Uses your saved OAuth keys + connected account; your settings are not changed.
Prints the video URL. Delete the test video in YouTube Studio afterwards if you like.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clipbot.config import load_settings, merge_incoming, resolve_paths  # noqa: E402
from clipbot.publishers.base import PostText, TokenStore  # noqa: E402
from clipbot.publishers.oauth import OAuthManager  # noqa: E402
from clipbot.publishers.youtube import YouTubePublisher  # noqa: E402

logger = logging.getLogger("clipbot.yt_test")


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = merge_incoming(load_settings(), {"posting": {"youtube": {"privacy": "private"}}})
    paths = resolve_paths(settings)
    video = Path(sys.argv[1]) if len(sys.argv) > 1 else max(
        paths.clips.glob("*.mp4"), key=lambda p: p.stat().st_mtime, default=None)
    if video is None or not video.exists():
        logger.error("no clip to upload (pass a path)")
        return 1
    async with httpx.AsyncClient() as http:
        tokens = TokenStore(paths.tokens)
        pub = YouTubePublisher(settings, http, tokens, OAuthManager(settings, http, tokens))
        ok, why = pub.configured()
        if not ok:
            logger.error("not ready: %s", why)
            return 1
        logger.info("uploading %s (%.1f MB) as PRIVATE…", video.name, video.stat().st_size / 1e6)
        res = await pub.publish(video, PostText(
            title="ClipBot upload test", caption="Private test upload from ClipBot. Safe to delete.",
            hashtags=["clipbot"]))
        if not res.ok:
            logger.error("FAILED: %s", res.error)
            return 1
        logger.info("OK: %s  (id %s)", res.url, res.remote_id)
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

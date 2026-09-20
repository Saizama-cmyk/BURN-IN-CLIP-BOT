"""Posting targets. ``build_publishers`` wires every platform with the shared OAuth manager."""
from __future__ import annotations

import httpx

from ..config import Settings
from .base import PostText, Publisher, PublishResult, TokenStore
from .discord import DiscordPublisher
from .facebook import FacebookPublisher
from .instagram import InstagramPublisher
from .oauth import OAuthManager
from .tiktok import TikTokPublisher
from .youtube import YouTubePublisher

PLATFORMS = ("youtube", "tiktok", "instagram", "facebook", "discord")

__all__ = ["PLATFORMS", "PostText", "Publisher", "PublishResult", "TokenStore", "OAuthManager",
           "build_publishers"]


def build_publishers(settings: Settings, http: httpx.AsyncClient, tokens: TokenStore,
                     oauth: OAuthManager) -> dict[str, Publisher]:
    return {
        "youtube": YouTubePublisher(settings, http, tokens, oauth),
        "tiktok": TikTokPublisher(settings, http, tokens, oauth),
        "instagram": InstagramPublisher(settings, http, tokens),
        "facebook": FacebookPublisher(settings, http, tokens),
        "discord": DiscordPublisher(settings, http, tokens),
    }

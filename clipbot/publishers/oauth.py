"""OAuth authorization-code flows for YouTube (Google) and TikTok, with auto-refresh.

Tokens live in ``tokens.json`` in the data folder. YouTube uses a loopback redirect
(``http://127.0.0.1:<port>/oauth/youtube/callback``), which Google allows for Desktop OAuth
clients. TikTok only accepts a registered https redirect, so ``accounts.tiktok_redirect_uri``
must point at a page that forwards ``?code&state`` to ``/oauth/tiktok/callback``.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from ..config import Settings
from ..util import ERR_SNIPPET
from .base import PublishError, TokenStore

logger = logging.getLogger("clipbot.oauth")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube"
TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_SCOPES = "user.info.basic,user.info.stats,video.list,video.publish,video.upload"
REFRESH_MARGIN_S = 120
PLATFORMS = ("youtube", "tiktok")


class OAuthManager:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, tokens: TokenStore) -> None:
        self.settings = settings
        self.http = http
        self.tokens = tokens
        self._states: dict[str, tuple[str, float]] = {}
        self._locks = {p: asyncio.Lock() for p in PLATFORMS}

    def apply(self, settings: Settings) -> None:
        self.settings = settings

    def local_redirect(self, platform: str) -> str:
        d = self.settings.dashboard
        return f"http://{d.host}:{d.port}/oauth/{platform}/callback"

    def redirect_uri(self, platform: str) -> str:
        if platform == "tiktok":
            return self.settings.accounts.tiktok_redirect_uri
        return self.local_redirect(platform)

    def connected(self, platform: str) -> bool:
        return bool(self.tokens.get(platform).get("refresh_token")
                    or self.tokens.get(platform).get("access_token"))

    def start_url(self, platform: str) -> str:
        a = self.settings.accounts
        state = secrets.token_urlsafe(24)
        self._states[state] = (platform, time.time())
        if platform == "youtube":
            if not (a.youtube_client_id and a.youtube_client_secret):
                raise PublishError("Add the YouTube OAuth client ID and secret in Settings → Accounts")
            return GOOGLE_AUTH_URL + "?" + urlencode({
                "client_id": a.youtube_client_id, "redirect_uri": self.redirect_uri(platform),
                "response_type": "code", "scope": YOUTUBE_SCOPES, "access_type": "offline",
                "prompt": "consent", "include_granted_scopes": "true", "state": state})
        if platform == "tiktok":
            if not (a.tiktok_client_key and a.tiktok_client_secret):
                raise PublishError("Add the TikTok client key and secret in Settings → Accounts")
            if not a.tiktok_redirect_uri.startswith("https://"):
                raise PublishError("TikTok needs an https redirect URI (Settings → Accounts)")
            return TIKTOK_AUTH_URL + "?" + urlencode({
                "client_key": a.tiktok_client_key, "scope": TIKTOK_SCOPES,
                "response_type": "code", "redirect_uri": a.tiktok_redirect_uri, "state": state})
        raise PublishError(f"no OAuth flow for {platform}")

    async def callback(self, platform: str, code: str, state: str) -> None:
        entry = self._states.pop(state, None)
        if not entry or entry[0] != platform:
            raise PublishError("OAuth state mismatch — start the connection again")
        a = self.settings.accounts
        if platform == "youtube":
            data = {"code": code, "client_id": a.youtube_client_id,
                    "client_secret": a.youtube_client_secret,
                    "redirect_uri": self.redirect_uri(platform), "grant_type": "authorization_code"}
            url = GOOGLE_TOKEN_URL
        else:
            data = {"code": code, "client_key": a.tiktok_client_key,
                    "client_secret": a.tiktok_client_secret,
                    "redirect_uri": self.redirect_uri(platform), "grant_type": "authorization_code"}
            url = TIKTOK_TOKEN_URL
        r = await self.http.post(url, data=data, timeout=self.settings.discovery.http_timeout_s)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or "access_token" not in body:
            raise PublishError(f"{platform} token exchange failed: {r.text[:ERR_SNIPPET]}")
        self._store(platform, body)
        logger.info("%s account connected", platform)

    async def callback_from_url(self, platform: str, landed_url: str) -> None:
        """Finish a sign-in from the address the browser ended up on (for when the redirect
        page cannot forward to BURN-IN): pulls ``code`` and ``state`` out of it."""
        q = parse_qs(urlsplit(landed_url.strip()).query)
        if q.get("error"):
            raise PublishError(f"{platform} sign-in was cancelled: "
                               f"{(q.get('error_description') or q['error'])[0]}")
        code, state = (q.get("code") or [""])[0], (q.get("state") or [""])[0]
        if not code or not state:
            raise PublishError("That address has no ?code=…&state=… — paste the full address "
                               "from the browser after approving")
        await self.callback(platform, code, state)

    def _store(self, platform: str, body: dict) -> None:
        old = self.tokens.get(platform)
        self.tokens.set(platform, {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token") or old.get("refresh_token", ""),
            "expires_at": time.time() + float(body.get("expires_in", 0) or 0),
            "open_id": body.get("open_id", old.get("open_id", "")),
            "scope": body.get("scope", old.get("scope", ""))})

    async def access_token(self, platform: str) -> str:
        async with self._locks[platform]:
            tok = self.tokens.get(platform)
            if not tok:
                raise PublishError(f"{platform} not connected — use Connect in Settings → Accounts")
            if tok.get("access_token") and time.time() < tok.get("expires_at", 0) - REFRESH_MARGIN_S:
                return tok["access_token"]
            if not tok.get("refresh_token"):
                raise PublishError(f"{platform} token expired and no refresh token — reconnect")
            a = self.settings.accounts
            if platform == "youtube":
                url, data = GOOGLE_TOKEN_URL, {
                    "client_id": a.youtube_client_id, "client_secret": a.youtube_client_secret,
                    "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}
            else:
                url, data = TIKTOK_TOKEN_URL, {
                    "client_key": a.tiktok_client_key, "client_secret": a.tiktok_client_secret,
                    "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}
            r = await self.http.post(url, data=data, timeout=self.settings.discovery.http_timeout_s)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code != 200 or "access_token" not in body:
                raise PublishError(f"{platform} token refresh failed: {r.text[:ERR_SNIPPET]}")
            self._store(platform, body)
            return body["access_token"]

    def disconnect(self, platform: str) -> None:
        self.tokens.clear(platform)

"""Find which live streams to watch on Twitch and Kick.

Order per platform: forced live streamers → forced categories → the top ``top_categories``
categories × ``streamers_per_category``; blocked streamers skipped, capped at ``slots``.
Missing credentials skip the platform with one warning; API errors keep the previous list.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import PlatformCfg, Settings
from .models import Platform, StreamTarget
from .util import ERR_SNIPPET

logger = logging.getLogger("clipbot.discovery")

# Protocol constants
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_API = "https://api.twitch.tv/helix"
KICK_TOKEN_URL = "https://id.kick.com/oauth/token"
KICK_API = "https://api.kick.com/public"
KICK_CHANNELS_MAX = 50        # API limit: slugs per /v1/channels call
KICK_V1_SEARCH_MAX = 100      # API limit: limit= on the (deprecated) v1 search
KICK_V1_GONE = (404, 410)     # v1 search retired → switch to v2 for good
KEYS_REJECTED = (400, 401)    # the token endpoint refused the client ID/secret
KICK_CHANNEL_V2 = "https://kick.com/api/v2/channels/{slug}"
# The feed kick.com's own front page uses: top live streams, sorted, no keys needed.
KICK_PUBLIC_LIVE = "https://web.kick.com/api/v1/livestreams"
KICK_PUBLIC_PAGE = 100          # streams per page of that feed
TOKEN_REFRESH_MARGIN_S = 60


class DiscoveryError(RuntimeError):
    pass


class KeysRejected(DiscoveryError):
    """The platform turned the client ID/secret down. Retrying cannot help until they change."""


class KickV1Gone(DiscoveryError):
    """Kick removed the deprecated, viewer-sorted v1 livestream search."""


@dataclass
class _Token:
    value: str = ""
    expires: float = 0.0

    def valid(self) -> bool:
        return bool(self.value) and time.time() < self.expires - TOKEN_REFRESH_MARGIN_S


def _dig(obj: Any, *keys: str, default: Any = None) -> Any:
    """Tolerant nested lookup: first key path that exists wins (keys may be dotted)."""
    for key in keys:
        cur = obj
        ok = True
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur and cur[part] is not None:
                cur = cur[part]
            else:
                ok = False
                break
        if ok:
            return cur
    return default


def _as_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def select_targets(cfg: PlatformCfg, forced_live: list[StreamTarget],
                   forced_cat_streams: list[list[StreamTarget]],
                   top_cat_streams: list[list[StreamTarget]],
                   per_category: int) -> list[StreamTarget]:
    """Pure selection logic shared by both platforms."""
    blocked = {b.strip().lower() for b in cfg.blocked_streamers if b.strip()}
    out: list[StreamTarget] = []
    seen: set[str] = set()

    def add(t: StreamTarget, forced: bool, filling: bool = False) -> bool:
        login = t.login.lower()
        if login in seen or login in blocked:
            return False
        if not forced and not filling and t.viewers < cfg.min_viewers:
            return False
        if len(out) >= cfg.slots:
            return False
        t.forced = forced
        seen.add(login)
        out.append(t)
        return True

    for t in sorted(forced_live, key=lambda s: -s.viewers):
        add(t, True)
    for group in forced_cat_streams + top_cat_streams:
        taken = 0
        for t in sorted(group, key=lambda s: -s.viewers):
            if taken >= per_category:
                break
            if add(t, False):
                taken += 1
    if cfg.fill_slots and len(out) < cfg.slots:
        # not enough streams cleared min_viewers: fill the rest with the biggest ones left
        for t in sorted((t for g in forced_cat_streams + top_cat_streams for t in g),
                        key=lambda s: -s.viewers):
            if len(out) >= cfg.slots:
                break
            add(t, False, filling=True)
    return out


class Discovery:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http
        self._twitch_token = _Token()
        self._kick_token = _Token()
        self._game_ids: dict[str, str] = {}
        self._kick_cat_ids: dict[str, int] = {}
        self._kick_channel_cache: dict[str, tuple[float, dict | None]] = {}
        self._kick_v1_search = True     # flips to v2 once Kick retires the sorted v1 search
        self.boosted: dict[str, list[str]] = {}   # platform -> logins analytics wants first
        self.top_game_ids: list[str] = []
        self.targets: dict[Platform, list[StreamTarget]] = {Platform.TWITCH: [], Platform.KICK: []}
        self.messages: dict[str, str] = {}
        self.last_refresh: float = 0.0
        self._warned: set[str] = set()
        self._rejected: set[str] = set()   # platforms whose current keys were refused

    def apply(self, settings: Settings) -> None:
        old = self.settings
        self.settings = settings
        if (old.twitch.client_id, old.twitch.client_secret) != (settings.twitch.client_id,
                                                                settings.twitch.client_secret):
            self._twitch_token = _Token()
            self._warned.discard("twitch")
            self._rejected.discard("twitch")
        if (old.kick.client_id, old.kick.client_secret) != (settings.kick.client_id,
                                                            settings.kick.client_secret):
            self._kick_token = _Token()
            self._warned.discard("kick")
            self._rejected.discard("kick")

    @property
    def _timeout(self) -> float:
        return self.settings.discovery.http_timeout_s

    # ------------------------------------------------------------------ public
    async def refresh(self) -> list[StreamTarget]:
        for platform, fn, cfg in ((Platform.TWITCH, self._twitch, self.settings.twitch),
                                  (Platform.KICK, self._kick, self.settings.kick)):
            name = str(platform)
            if not cfg.enabled:
                self.targets[platform] = []
                self.messages[name] = f"{name.title()}: disabled in Settings"
                continue
            keyless = platform == Platform.KICK and (
                not (cfg.client_id and cfg.client_secret) or name in self._rejected)
            if keyless:
                try:
                    self.targets[platform] = await self._kick(cfg, public=True)
                    note = ("keys rejected - using Kick's public list"
                            if name in self._rejected else "no keys needed")
                    self.messages[name] = (f"Kick: {len(self.targets[platform])} streams "
                                           f"selected ({note})")
                except (httpx.HTTPError, DiscoveryError, ValueError, KeyError) as exc:
                    logger.warning("kick public discovery failed, keeping previous list: %s", exc)
                    self.messages[name] = f"Kick: public list unavailable ({exc})"
                continue
            if not (cfg.client_id and cfg.client_secret):
                self.targets[platform] = []
                self.messages[name] = (f"{name.title()}: no client ID/secret — add them in "
                                       f"Settings → {name.title()}")
                if name not in self._warned:
                    self._warned.add(name)
                    logger.warning("%s discovery skipped: missing client ID/secret", name)
                continue
            if name in self._rejected:
                continue                       # same keys as last time: asking again cannot work
            try:
                self.targets[platform] = await fn(cfg)
                self.messages[name] = (f"{name.title()}: {len(self.targets[platform])} streams "
                                       f"selected")
            except KeysRejected as exc:
                # Asking every cycle with keys the platform has already refused only fills the
                # log and spends a request. Say it once, plainly, and wait for new keys - the
                # settings change clears this and discovery resumes on its own.
                self._rejected.add(name)
                logger.warning("%s rejected the client ID/secret; not asking again until they "
                               "change: %s", name, exc)
                self.messages[name] = (f"{name.title()}: keys rejected - paste the client ID and "
                                       f"secret again in Settings → {name.title()}")
                if platform == Platform.KICK:
                    try:
                        self.targets[platform] = await self._kick(cfg, public=True)
                        self.messages[name] = (f"Kick: {len(self.targets[platform])} streams "
                                               f"selected (keys rejected - using Kick's public list)")
                    except (httpx.HTTPError, DiscoveryError, ValueError, KeyError) as exc2:
                        logger.warning("kick public discovery failed too: %s", exc2)
            except (httpx.HTTPError, DiscoveryError, ValueError, KeyError) as exc:
                logger.warning("%s discovery failed, keeping previous list: %s", name, exc)
                self.messages[name] = f"{name.title()}: API error ({exc}); keeping previous list"
        self.last_refresh = time.time()
        return self.targets[Platform.TWITCH] + self.targets[Platform.KICK]

    # ------------------------------------------------------------------ twitch
    async def _twitch_auth(self, cfg: PlatformCfg) -> dict[str, str]:
        if not self._twitch_token.valid():
            r = await self.http.post(TWITCH_TOKEN_URL, timeout=self._timeout, data={
                "client_id": cfg.client_id, "client_secret": cfg.client_secret,
                "grant_type": "client_credentials"})
            if r.status_code != 200:
                raise DiscoveryError(f"Twitch token HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
            body = r.json()
            self._twitch_token = _Token(body["access_token"],
                                        time.time() + float(body.get("expires_in", 0)))
        return {"Client-Id": cfg.client_id, "Authorization": f"Bearer {self._twitch_token.value}"}

    async def _helix(self, cfg: PlatformCfg, path: str, params: list[tuple[str, Any]]) -> list[dict]:
        for attempt in range(2):
            headers = await self._twitch_auth(cfg)
            r = await self.http.get(f"{TWITCH_API}/{path}", params=params, headers=headers,
                                    timeout=self._timeout)
            if r.status_code == 401 and attempt == 0:
                self._twitch_token = _Token()
                continue
            if r.status_code == 429:
                reset = _as_int(r.headers.get("Ratelimit-Reset")) - time.time()
                raise DiscoveryError(f"Twitch rate limited (reset in {max(0, int(reset))}s)")
            if r.status_code != 200:
                raise DiscoveryError(f"Twitch {path} HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
            data = r.json().get("data", [])
            return data if isinstance(data, list) else []
        raise DiscoveryError("Twitch rejected the app token twice (check client ID/secret)")

    @staticmethod
    def _twitch_target(s: dict) -> StreamTarget:
        return StreamTarget(platform=Platform.TWITCH, login=str(s.get("user_login", "")),
                            user_id=str(s.get("user_id", "")),
                            display_name=str(s.get("user_name") or s.get("user_login", "")),
                            category=str(s.get("game_name", "")),
                            viewers=_as_int(s.get("viewer_count")), title=str(s.get("title", "")))

    async def _twitch_streams(self, cfg: PlatformCfg, game_id: str) -> list[StreamTarget]:
        params: list[tuple[str, Any]] = [("game_id", game_id),
                                         ("first", self.settings.discovery.fetch_limit)]
        params += [("language", lang) for lang in cfg.languages if lang.strip()]
        return [self._twitch_target(s) for s in await self._helix(cfg, "streams", params)
                if s.get("user_login")]

    async def _twitch_game_id(self, cfg: PlatformCfg, name: str) -> str | None:
        key = name.strip().lower()
        if key not in self._game_ids:
            data = await self._helix(cfg, "games", [("name", name.strip())])
            if not data:
                logger.warning("Twitch category %r not found (check the spelling on twitch.tv)", name)
                self._game_ids[key] = None           # remember the miss: warn and query once
                return None
            self._game_ids[key] = str(data[0]["id"])
        return self._game_ids[key]

    async def _twitch(self, cfg: PlatformCfg) -> list[StreamTarget]:
        d = self.settings.discovery
        limit = d.fetch_limit
        forced_logins = list(dict.fromkeys(
            [f.strip().lower() for f in cfg.forced_streamers if f.strip()]
            + self.boosted.get("twitch", [])))
        forced_live: list[StreamTarget] = []
        for i in range(0, len(forced_logins), limit):
            chunk = forced_logins[i:i + limit]
            data = await self._helix(cfg, "streams", [("user_login", x) for x in chunk] +
                                     [("first", limit)])
            forced_live += [self._twitch_target(s) for s in data if s.get("user_login")]
        forced_groups = []
        for cat in cfg.forced_categories:
            if cat.strip():
                gid = await self._twitch_game_id(cfg, cat)
                if gid:
                    forced_groups.append(await self._twitch_streams(cfg, gid))
        games = await self._helix(cfg, "games/top", [("first", d.top_categories)])
        self.top_game_ids = [str(g["id"]) for g in games]
        top_groups = await asyncio.gather(*(self._twitch_streams(cfg, str(g["id"]))
                                            for g in games[:d.top_categories]))
        return select_targets(cfg, forced_live, forced_groups, list(top_groups),
                              d.streamers_per_category)

    async def twitch_clips(self, *, game_id: str = "", broadcaster_id: str = "",
                           started_at: str, first: int) -> list[dict]:
        """Most-viewed clips (Helix returns them by view count) for a game or a broadcaster."""
        cfg = self.settings.twitch
        if not (cfg.enabled and cfg.client_id and cfg.client_secret):
            return []
        params: list[tuple[str, Any]] = [("started_at", started_at), ("first", first)]
        params.append(("game_id", game_id) if game_id else ("broadcaster_id", broadcaster_id))
        return await self._helix(cfg, "clips", params)

    # ------------------------------------------------------------------ kick
    async def _kick_auth(self, cfg: PlatformCfg) -> dict[str, str]:
        if not self._kick_token.valid():
            r = await self.http.post(KICK_TOKEN_URL, timeout=self._timeout, data={
                "client_id": cfg.client_id, "client_secret": cfg.client_secret,
                "grant_type": "client_credentials"})
            if r.status_code in KEYS_REJECTED:
                raise KeysRejected(f"Kick token HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
            if r.status_code != 200:
                raise DiscoveryError(f"Kick token HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
            body = r.json()
            self._kick_token = _Token(body["access_token"],
                                      time.time() + float(body.get("expires_in", 0)))
        return {"Authorization": f"Bearer {self._kick_token.value}", "Accept": "application/json"}

    async def _kick_get(self, cfg: PlatformCfg, path: str, params: list[tuple[str, Any]]) -> Any:
        body = await self._kick_body(cfg, path, params)
        return body.get("data", body) if isinstance(body, dict) else body

    async def _kick_body(self, cfg: PlatformCfg, path: str, params: list[tuple[str, Any]]) -> Any:
        for attempt in range(2):
            headers = await self._kick_auth(cfg)
            r = await self.http.get(f"{KICK_API}/{path}", params=params, headers=headers,
                                    timeout=self._timeout)
            if r.status_code == 401 and attempt == 0:
                self._kick_token = _Token()
                continue
            if r.status_code in KICK_V1_GONE and path.startswith("v1/livestreams"):
                raise KickV1Gone(path)
            if r.status_code != 200:
                raise DiscoveryError(f"Kick {path} HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
            return r.json()
        raise DiscoveryError("Kick rejected the app token twice (check client ID/secret)")

    @staticmethod
    def _kick_target(s: dict) -> StreamTarget | None:
        slug = _dig(s, "slug", "channel.slug", "broadcaster.slug", "channel_slug")
        if not slug:
            return None
        return StreamTarget(
            platform=Platform.KICK, login=str(slug),
            display_name=str(_dig(s, "broadcaster_user.username", "broadcaster.username",
                                  "channel.username", "user.username", "username", default=slug)),
            category=str(_dig(s, "category.name", "categories.0.name", default="")),
            viewers=_as_int(_dig(s, "viewer_count", "viewers", "stream.viewer_count", default=0)),
            title=str(_dig(s, "stream_title", "session_title", "title", default="")))

    @staticmethod
    def _kick_category_id(s: dict) -> int | None:
        v = _dig(s, "category.id", "category_id")
        return _as_int(v) if v is not None else None

    def _kick_lang_ok(self, cfg: PlatformCfg, s: dict) -> bool:
        langs = {x.strip().lower() for x in cfg.languages if x.strip()}
        lang = str(_dig(s, "language_code", "language", "stream.language", default="")).lower()
        return not langs or not lang or lang in langs

    async def _kick_livestreams(self, cfg: PlatformCfg, category_id: int | None) -> list[dict]:
        """Live streams (optionally in one category), most-watched first."""
        if self._kick_v1_search:
            try:
                return await self._kick_livestreams_v1(category_id)
            except KickV1Gone:
                logger.info("Kick v1 livestream search is gone; using v2 with paging")
                self._kick_v1_search = False
        return await self._kick_livestreams_v2(cfg, category_id)

    async def _kick_livestreams_v1(self, category_id: int | None) -> list[dict]:
        cfg = self.settings.kick
        params: list[tuple[str, Any]] = [
            ("limit", min(self.settings.discovery.fetch_limit, KICK_V1_SEARCH_MAX)),
            ("sort", "viewer_count")]
        if category_id is not None:
            params.append(("category_id", category_id))
        data = await self._kick_get(cfg, "v1/livestreams", params)
        return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []

    async def _kick_livestreams_v2(self, cfg: PlatformCfg, category_id: int | None) -> list[dict]:
        """v2 returns oldest-first with no sort option: page (capped) and sort by viewers."""
        d = self.settings.discovery
        base: list[tuple[str, Any]] = [("limit", d.kick_page_size)]
        if category_id is not None:
            base.append(("category_id", category_id))
        base += [("language_code", x.strip()) for x in cfg.languages if x.strip()]
        out: list[dict] = []
        cursor = ""
        for _ in range(d.kick_max_pages):
            body = await self._kick_body(cfg, "v2/livestreams",
                                         base + ([("cursor", cursor)] if cursor else []))
            data = body.get("data", []) if isinstance(body, dict) else body
            out += [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []
            cursor = _dig(body, "pagination.next_cursor", default="") if isinstance(body, dict) else ""
            if not cursor:
                break
        return sorted(out, key=lambda s: -_as_int(_dig(s, "viewer_count", default=0)))

    async def _kick_category(self, cfg: PlatformCfg, name: str) -> int | None:
        key = name.strip().lower()
        if key not in self._kick_cat_ids:
            data = await self._kick_get(cfg, "v2/categories", [("name", name.strip())])
            items = data if isinstance(data, list) else []
            match = next((c for c in items if str(c.get("name", "")).lower() == key),
                         items[0] if items else None)
            if not match:
                logger.warning("Kick category %r not found (check the spelling on kick.com)", name)
                self._kick_cat_ids[key] = None
                return None
            self._kick_cat_ids[key] = _as_int(match.get("id"))
        return self._kick_cat_ids[key]

    def _public_headers(self) -> dict[str, str]:
        return {"User-Agent": self.settings.discovery.user_agent,
                "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://kick.com/", "Origin": "https://kick.com"}

    async def _kick_public_live(self, cfg: PlatformCfg) -> list[dict]:
        """Top live Kick streams from the public front-page feed, most-watched first."""
        d = self.settings.discovery
        langs = [x.strip() for x in cfg.languages if x.strip()]
        out: list[dict] = []
        for lang in (langs or [""]):
            cursor = ""
            for _ in range(d.kick_max_pages):
                params: list[tuple[str, Any]] = [("limit", KICK_PUBLIC_PAGE),
                                                 ("sort", "viewer_count_desc")]
                if lang:
                    params.append(("language", lang))
                if cursor:
                    params.append(("cursor", cursor))
                r = await self.http.get(KICK_PUBLIC_LIVE, params=params,
                                        headers=self._public_headers(), timeout=self._timeout)
                if r.status_code != 200:
                    raise DiscoveryError(f"Kick public list HTTP {r.status_code}")
                raw = r.json()
                body = raw.get("data") if isinstance(raw, dict) else None
                body = body if isinstance(body, dict) else {}
                page = [x for x in body.get("livestreams") or [] if isinstance(x, dict)]
                out += page
                cursor = _dig(body, "pagination.next_cursor", default="")
                if not cursor or len(out) >= d.fetch_limit * len(langs or [""]) or not page:
                    break
        return sorted(out, key=lambda s: -_as_int(_dig(s, "viewer_count", default=0)))

    async def _kick_public_forced(self, slugs: list[str]) -> list[StreamTarget]:
        """Forced/boosted Kick channels that are live, via the public channel page."""
        live: list[StreamTarget] = []
        for slug in slugs:
            try:
                r = await self.http.get(KICK_CHANNEL_V2.format(slug=slug),
                                        headers=self._public_headers(), timeout=self._timeout)
                body = r.json() if r.status_code == 200 else {}
            except (httpx.HTTPError, ValueError):
                continue
            stream = body.get("livestream") if isinstance(body, dict) else None
            if not stream:
                continue
            live.append(StreamTarget(
                platform=Platform.KICK, login=slug,
                display_name=str(_dig(body, "user.username", default=slug)),
                category=str(_dig(stream, "categories.0.name", default="")),
                viewers=_as_int(stream.get("viewer_count", 0)),
                title=str(stream.get("session_title", ""))))
        return live

    async def _kick(self, cfg: PlatformCfg, public: bool = False) -> list[StreamTarget]:
        d = self.settings.discovery
        forced_live: list[StreamTarget] = []
        slugs = list(dict.fromkeys([f.strip().lower() for f in cfg.forced_streamers if f.strip()]
                                   + self.boosted.get("kick", [])))
        if public:
            forced_live = await self._kick_public_forced(slugs)
            slugs = []
        for i in range(0, len(slugs), KICK_CHANNELS_MAX):
            data = await self._kick_get(cfg, "v1/channels",
                                        [("slug", s) for s in slugs[i:i + KICK_CHANNELS_MAX]])
            for ch in data if isinstance(data, list) else []:
                if not _dig(ch, "stream.is_live", default=False):
                    continue
                t = self._kick_target(ch)
                if t:
                    t.viewers = _as_int(_dig(ch, "stream.viewer_count", default=t.viewers))
                    forced_live.append(t)
        forced_groups = []
        public_top = await self._kick_public_live(cfg) if public else None
        for cat in cfg.forced_categories if public else []:
            key = cat.strip().lower()
            if key:
                forced_groups.append([t for s in public_top
                                      if str(_dig(s, "category.name", default="")).lower() == key
                                      and self._kick_lang_ok(cfg, s) and (t := self._kick_target(s))])
        for cat in cfg.forced_categories if not public else []:
            if cat.strip():
                cid = await self._kick_category(cfg, cat)
                if cid is not None:
                    raw = await self._kick_livestreams(cfg, cid)
                    forced_groups.append([t for s in raw if self._kick_lang_ok(cfg, s)
                                          and (t := self._kick_target(s))])
        # Kick has no "top categories" endpoint: rank categories by viewers in the top list.
        raw_top = public_top if public else await self._kick_livestreams(cfg, None)
        top = [s for s in raw_top if self._kick_lang_ok(cfg, s)]
        by_cat: dict[int, list[dict]] = {}
        totals: dict[int, int] = {}
        for s in top:
            cid = self._kick_category_id(s)
            if cid is None:
                continue
            by_cat.setdefault(cid, []).append(s)
            totals[cid] = totals.get(cid, 0) + _as_int(_dig(s, "viewer_count", default=0))
        ranked = sorted(totals, key=lambda c: -totals[c])[:d.top_categories]
        top_groups = [[t for s in by_cat[c] if (t := self._kick_target(s))] for c in ranked]
        return select_targets(cfg, forced_live, forced_groups, top_groups,
                              d.streamers_per_category)

    async def kick_channel(self, slug: str) -> dict | None:
        """Public v2 channel JSON (chatroom id, playback_url). None if Cloudflare blocks it."""
        now = time.time()
        cached = self._kick_channel_cache.get(slug)
        if cached and now - cached[0] < self.settings.discovery.interval_s:
            return cached[1]
        headers = {"User-Agent": self.settings.discovery.user_agent,
                   "Accept": "application/json, text/plain, */*",
                   "Accept-Language": "en-US,en;q=0.9", "Referer": f"https://kick.com/{slug}"}
        info: dict | None = None
        try:
            r = await self.http.get(KICK_CHANNEL_V2.format(slug=slug), headers=headers,
                                    timeout=self._timeout)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith(
                    "application/json"):
                body = r.json()
                info = {"chatroom_id": _as_int(_dig(body, "chatroom.id")) or None,
                        "playback_url": _dig(body, "playback_url", default="")}
            else:
                logger.info("Kick channel %s lookup blocked/failed (HTTP %s)", slug, r.status_code)
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("Kick channel %s lookup failed: %s", slug, exc)
        self._kick_channel_cache[slug] = (now, info)
        return info

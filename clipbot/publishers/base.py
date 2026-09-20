"""Publisher protocol, results, post text and the shared token store."""
from __future__ import annotations

import asyncio
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..config import Settings, load_tokens, save_tokens

logger = logging.getLogger("clipbot.publishers")


@dataclass
class PostText:
    title: str
    caption: str
    hashtags: list[str] = field(default_factory=list)


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def stats(views=0, likes=0, comments=0, shares=0, saves=0) -> dict:
    return {"views": _int(views), "likes": _int(likes), "comments": _int(comments),
            "shares": _int(shares), "saves": _int(saves)}


@dataclass
class PublishResult:
    ok: bool
    remote_id: str = ""
    url: str = ""
    error: str = ""


class PublishError(RuntimeError):
    pass


class TokenStore:
    """tokens.json access shared by the OAuth flows and publishers (thread-safe)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def get(self, platform: str) -> dict:
        with self._lock:
            return dict(load_tokens(self.path).get(platform, {}))

    def set(self, platform: str, data: dict) -> None:
        with self._lock:
            tokens = load_tokens(self.path)
            tokens[platform] = data
            save_tokens(self.path, tokens)

    def clear(self, platform: str) -> None:
        with self._lock:
            tokens = load_tokens(self.path)
            tokens.pop(platform, None)
            save_tokens(self.path, tokens)


async def read_bytes(path: Path) -> bytes:
    return await asyncio.to_thread(Path(path).read_bytes)


def http_error(r: httpx.Response, what: str, snippet: int) -> PublishError:
    return PublishError(f"{what} HTTP {r.status_code}: {r.text[:snippet]}")


class Publisher(ABC):
    name = ""

    def __init__(self, settings: Settings, http: httpx.AsyncClient, tokens: TokenStore) -> None:
        self.settings = settings
        self.http = http
        self.tokens = tokens

    def apply(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def timeout(self) -> float:
        return self.settings.posting.upload_timeout_s

    @abstractmethod
    def configured(self) -> tuple[bool, str]:
        """(ready, reason shown in the dashboard when not ready)."""

    @abstractmethod
    async def _publish(self, video: Path, post: PostText) -> PublishResult:
        """Upload; raise PublishError / httpx.HTTPError on failure."""

    async def publish(self, video: Path, post: PostText) -> PublishResult:
        ok, reason = self.configured()
        if not ok:
            return PublishResult(False, error=reason)
        try:
            return await self._publish(video, post)
        except (PublishError, httpx.HTTPError, OSError, ValueError, KeyError) as exc:
            logger.warning("%s publish failed: %s", self.name, exc)
            return PublishResult(False, error=str(exc) or type(exc).__name__)

    async def metrics(self, remote_ids: list[str]) -> dict[str, dict]:
        """{remote_id: {views, likes, comments, shares, saves}} for posted items.
        Platforms without a stats API return {}."""
        return {}

    async def account(self) -> dict | None:
        """{followers, total_views, posts} for the connected account, or None."""
        return None

    async def delete(self, remote_id: str) -> PublishResult:
        """Remove a published post. Platforms without a delete API report that plainly."""
        return PublishResult(False, error=f"{self.name} has no delete API")

    async def poll(self, check, what: str):
        """Call ``check()`` until it returns a non-None value or polls run out."""
        p = self.settings.posting
        for _ in range(p.status_poll_max):
            result = await check()
            if result is not None:
                return result
            await asyncio.sleep(p.status_poll_s)
        raise PublishError(f"{what}: still processing after {p.status_poll_max} polls")

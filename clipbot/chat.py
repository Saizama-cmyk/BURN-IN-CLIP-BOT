"""Anonymous chat readers: one Twitch IRC websocket and one Kick Pusher websocket.

Both keep a desired channel set (``set_targets``) and join/part to match it, reconnecting
forever with exponential backoff. Every chat line is handed to ``on_message(key, text, t)``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from .config import Settings
from .models import Platform, StreamTarget
from .util import Backoff

logger = logging.getLogger("clipbot.chat")

# Protocol constants
TWITCH_IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
TWITCH_ANON_PASS = "SCHMOOPIIE"
# Twitch only accepts anonymous (read-only) logins as justinfan<digits>; any other nick is
# refused with "Improperly formatted auth" and dropped ~10 s later. Protocol, not a setting.
TWITCH_ANON_NICK = "justinfan"
TWITCH_NICK_DIGITS = (10000, 99999)
KICK_PUSHER_URL = ("wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js"
                   "&version=8.4.0&flash=false")
KICK_CHAT_EVENT = "App\\Events\\ChatMessageEvent"

OnMessage = Callable[[str, str, float], None]
ChatroomLookup = Callable[[str], Awaitable[dict | None]]


_PRIVMSG_RE = re.compile(r"^(?:@\S+ )?:\S+ PRIVMSG #(?P<chan>\S+) :?(?P<text>.*)$")
_ACTION_RE = re.compile(r"^\x01ACTION (?P<text>.*)\x01$")


def parse_privmsg(line: str) -> tuple[str, str] | None:
    """Return (channel, text) for an IRC PRIVMSG line (tags tolerated), else None."""
    m = _PRIVMSG_RE.match(line)
    if not m:
        return None
    text = m["text"]
    action = _ACTION_RE.match(text)
    return m["chan"].lower(), action["text"] if action else text


def irc_command(line: str) -> str:
    if line.startswith("@"):
        _, _, line = line.partition(" ")
    parts = line.split(" ", 2)
    if line.startswith(":"):
        return parts[1] if len(parts) > 1 else ""
    return parts[0]


def parse_kick_event(raw: str) -> tuple[str, str | None, dict | str | None]:
    """Return (event, channel, data) for a Pusher frame; data JSON-decoded when possible."""
    msg = json.loads(raw)
    data = msg.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            pass
    return msg.get("event", ""), msg.get("channel"), data


class _Client(ABC):
    name = "chat"

    def __init__(self, settings: Settings, on_message: OnMessage) -> None:
        self.settings = settings
        self.on_message = on_message
        self.desired: dict[str, str] = {}     # channel id → target key
        self.joined: set[str] = set()
        self.connected = False
        self.last_error = ""
        self.messages = 0
        self._ws: ClientConnection | None = None
        self._sync = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"chat:{self.name}")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.connected = False

    def set_channels(self, desired: dict[str, str]) -> None:
        self.desired = desired
        self._sync.set()
        if desired:
            self.start()

    def snapshot(self) -> dict:
        return {"connected": self.connected, "joined": len(self.joined),
                "wanted": len(self.desired), "messages": self.messages, "error": self.last_error}

    async def _run(self) -> None:
        c = self.settings.chat
        backoff = Backoff(c.reconnect_min_s, c.reconnect_max_s)
        while True:
            try:
                async with connect(self.url(), ping_interval=c.ping_interval_s,
                                   ping_timeout=c.ping_interval_s, max_size=None) as ws:
                    self._ws = ws
                    self.joined = set()
                    await self.handshake(ws)
                    self.connected = True
                    self.last_error = ""
                    backoff.reset()
                    self._sync.set()
                    syncer = asyncio.create_task(self._sync_loop(ws))
                    try:
                        async for raw in ws:
                            await self.handle(ws, raw if isinstance(raw, str)
                                              else raw.decode("utf-8", "replace"))
                    finally:
                        syncer.cancel()
                        await asyncio.gather(syncer, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except (OSError, WebSocketException, asyncio.TimeoutError, ValueError) as exc:
                self.last_error = str(exc) or type(exc).__name__
                logger.warning("%s chat disconnected: %s", self.name, self.last_error)
            finally:
                self._ws = None
                self.connected = False
                self.joined = set()
            delay = backoff.next()
            logger.info("%s chat reconnecting in %.0fs", self.name, delay)
            await asyncio.sleep(delay)

    async def _sync_loop(self, ws: ClientConnection) -> None:
        while True:
            await self._sync.wait()
            self._sync.clear()
            for ch in [c for c in self.joined if c not in self.desired]:
                await self.part(ws, ch)
                self.joined.discard(ch)
            for ch in [c for c in self.desired if c not in self.joined]:
                if ch not in self.desired:
                    continue
                await self.join(ws, ch)
                self.joined.add(ch)
                await asyncio.sleep(self.settings.chat.join_spacing_s)

    def deliver(self, channel: str, text: str) -> None:
        key = self.desired.get(channel)
        if key is None:
            return
        self.messages += 1
        self.on_message(key, text, time.time())

    # platform hooks
    @abstractmethod
    def url(self) -> str: ...

    @abstractmethod
    async def handshake(self, ws: ClientConnection) -> None: ...

    @abstractmethod
    async def handle(self, ws: ClientConnection, raw: str) -> None: ...

    @abstractmethod
    async def join(self, ws: ClientConnection, channel: str) -> None: ...

    @abstractmethod
    async def part(self, ws: ClientConnection, channel: str) -> None: ...


class TwitchChat(_Client):
    name = "twitch"

    def url(self) -> str:
        return TWITCH_IRC_URL

    async def handshake(self, ws: ClientConnection) -> None:
        nick = f"{TWITCH_ANON_NICK}{random.randint(*TWITCH_NICK_DIGITS)}"
        await ws.send(f"PASS {TWITCH_ANON_PASS}")
        await ws.send(f"NICK {nick}")

    async def handle(self, ws: ClientConnection, raw: str) -> None:
        for line in raw.split("\r\n"):
            if not line:
                continue
            if line.startswith("PING"):
                await ws.send("PONG" + line[len("PING"):])
                continue
            if " NOTICE * :" in line and "auth" in line.lower():   # login refused (not a channel notice)
                raise ConnectionRefusedError(f"twitch refused the anonymous login: {line}")
            if irc_command(line) == "RECONNECT":
                raise ConnectionResetError("server requested RECONNECT")
            parsed = parse_privmsg(line)
            if parsed:
                self.deliver(*parsed)

    async def join(self, ws: ClientConnection, channel: str) -> None:
        await ws.send(f"JOIN #{channel}")

    async def part(self, ws: ClientConnection, channel: str) -> None:
        await ws.send(f"PART #{channel}")


class KickChat(_Client):
    name = "kick"

    def url(self) -> str:
        return KICK_PUSHER_URL

    async def handshake(self, ws: ClientConnection) -> None:
        return None  # pusher:connection_established arrives on its own

    async def handle(self, ws: ClientConnection, raw: str) -> None:
        try:
            event, channel, data = parse_kick_event(raw)
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.debug("kick: unparseable frame (%s): %.120s", exc, raw)
            return
        if event == "pusher:ping":
            await ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
        elif event == "pusher:error":
            self.last_error = str(data)
            logger.warning("kick pusher error: %s", data)
        elif event == KICK_CHAT_EVENT and channel and isinstance(data, dict):
            content = data.get("content")
            if isinstance(content, str):
                self.deliver(channel, content)

    async def join(self, ws: ClientConnection, channel: str) -> None:
        await ws.send(json.dumps({"event": "pusher:subscribe",
                                  "data": {"auth": "", "channel": channel}}))

    async def part(self, ws: ClientConnection, channel: str) -> None:
        await ws.send(json.dumps({"event": "pusher:unsubscribe", "data": {"channel": channel}}))


class ChatHub:
    def __init__(self, settings: Settings, on_message: OnMessage,
                 kick_lookup: ChatroomLookup | None) -> None:
        self.settings = settings
        self.twitch = TwitchChat(settings, on_message)
        self.kick = KickChat(settings, on_message)
        self.kick_lookup = kick_lookup
        self.no_chat: set[str] = set()

    def apply(self, settings: Settings) -> None:
        self.settings = settings
        self.twitch.settings = settings
        self.kick.settings = settings

    async def set_targets(self, targets: list[StreamTarget]) -> None:
        tw = {t.login.lower(): t.key for t in targets if t.platform == Platform.TWITCH}
        kk: dict[str, str] = {}
        self.no_chat = set()
        for t in targets:
            if t.platform != Platform.KICK:
                continue
            if t.chatroom_id is None and self.kick_lookup is not None:
                info = await self.kick_lookup(t.login)
                t.chatroom_id = (info or {}).get("chatroom_id")
            if t.chatroom_id:
                kk[f"chatrooms.{t.chatroom_id}.v2"] = t.key
            else:
                self.no_chat.add(t.key)
        if self.no_chat:
            logger.info("kick chat unavailable for %s (chatroom lookup blocked)",
                        ", ".join(sorted(self.no_chat)))
        self.twitch.set_channels(tw)
        self.kick.set_channels(kk)

    async def stop(self) -> None:
        await asyncio.gather(self.twitch.stop(), self.kick.stop())

    def snapshot(self) -> dict:
        return {"twitch": self.twitch.snapshot(), "kick": self.kick.snapshot(),
                "no_chat": sorted(self.no_chat)}

"""The Ashvane runtime: settings + pipeline + dashboard server in one asyncio loop.

Used by every entry point (console dev mode, desktop window, headless exe). The desktop shell
runs ``ClipBotApp.run()`` on a background thread and calls ``request_quit()`` from the tray.
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import secrets
import socket
import sys
import threading
from pathlib import Path
from typing import Callable

import uvicorn

from .autostart import get_autostart, launch_command, set_autostart
from .config import (APP_NAME, RESTART_FIELDS, Settings, changed_paths, is_frozen,
                     resolve_paths, save_settings)
from .pipeline import Pipeline
from .storage import apply_pending

logger = logging.getLogger("clipbot.app")

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LAN_PROBE = "192.0.2.1"      # TEST-NET-1: routable-looking, never actually contacted
LAN_PROBE_PORT = 9
STARTUP_POLL_S = 0.05   # how often we check that uvicorn finished binding


def setup_logging(settings: Settings, console: bool) -> Path:
    paths = resolve_paths(settings)
    paths.logs.mkdir(parents=True, exist_ok=True)
    log_file = paths.logs / "clipbot.log"
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=settings.app.log_max_mb * 1024 * 1024,
        backupCount=settings.app.log_backups, encoding="utf-8")
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(fh)
    if console and sys.stderr is not None:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(ch)
    root.setLevel(settings.app.log_level)
    logging.captureWarnings(True)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    for noisy in ("httpx", "httpcore", "faster_whisper", "websockets"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))
    return log_file


def _quiet_disconnects(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Windows' proactor loop logs a full traceback whenever a browser drops a connection
    (WinError 10054). That's normal for a dashboard; log it at debug, everything else as usual."""
    exc = context.get("exception")
    if isinstance(exc, ConnectionResetError):
        logger.debug("client disconnected: %s", exc)
        return
    loop.default_exception_handler(context)


ANY_HOST = "0.0.0.0"        # every interface, so a phone on the same Wi-Fi can reach it


def lan_ip() -> str:
    """This PC's address on the local network (no traffic is sent; the socket just picks a route)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((LAN_PROBE, LAN_PROBE_PORT))
            return s.getsockname()[0]
        except OSError:
            return socket.gethostbyname(socket.gethostname())


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


class ClipBotApp:
    def __init__(self, settings: Settings, dashboard: bool = True) -> None:
        self.settings = settings
        self.move_notes = apply_pending(settings)   # folders picked on the Storage page
        self.paths = resolve_paths(settings)
        self.dashboard = dashboard
        self.pipeline = Pipeline(settings, self.paths)
        self.restart_pending: set[str] = set()
        self.restart_requested = False
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.on_focus: Callable[[], None] | None = None
        self.pick_folder: Callable[[], str | None] | None = None
        self.on_quit: Callable[[], None] | None = None
        # the pet window's key for /api/pet/*; CLIPBOT_PET_KEY pins it for local testing
        self.pet_token = os.environ.get("CLIPBOT_PET_KEY") or secrets.token_urlsafe(24)
        self.on_state_change: Callable[[], None] | None = None
        self.error: str = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._quit: asyncio.Event | None = None
        self._server: uvicorn.Server | None = None

    @property
    def url(self) -> str:
        d = self.settings.dashboard
        return f"http://{d.host}:{d.port}/"

    @property
    def lan_url(self) -> str:
        """Address to type on a phone: this PC's address on the local network."""
        return f"http://{lan_ip()}:{self.settings.dashboard.port}/remote"

    # ------------------------------------------------------------------ run
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop.set_exception_handler(_quiet_disconnects)
        self._quit = asyncio.Event()
        d = self.settings.dashboard
        server_task = None
        try:
            if self.dashboard:
                if not port_free(ANY_HOST if d.remote else d.host, d.port):
                    self.error = (f"port {d.port} on {d.host} is already in use — another program "
                                  f"(or Ashvane) is running there. Change Settings → Dashboard → Port.")
                    logger.error(self.error)
                    return
                from .dashboard.server import create_app

                bind = ANY_HOST if d.remote else d.host
                config = uvicorn.Config(create_app(self), host=bind, port=d.port,
                                        log_level="warning", access_log=False, lifespan="off")
                self._server = uvicorn.Server(config)
                server_task = asyncio.create_task(self._server.serve(), name="uvicorn")
            if is_frozen():  # the installed exe owns the Run key; dev runs only touch it on toggle
                self.sync_autostart()
            await self.pipeline.start()
            if self._server:
                while not self._server.started and not server_task.done():
                    await asyncio.sleep(STARTUP_POLL_S)
                if server_task.done():
                    self.error = "dashboard server failed to start (see log)"
                    logger.error(self.error)
                    return
                logger.info("dashboard at %s", self.url)
                if d.remote:
                    logger.info("phone remote at %s (same Wi-Fi, sign in with your profile)",
                                self.lan_url)
            self.ready.set()
            await self._quit.wait()
        finally:
            self.ready.set()
            if self._server:
                self._server.should_exit = True
            if server_task:
                await asyncio.gather(server_task, return_exceptions=True)
            await self.pipeline.stop()
            self.stopped.set()
            logger.info("%s stopped", APP_NAME)

    def request_quit(self, restart: bool = False) -> None:
        """Thread-safe: stop the loop (optionally flag a relaunch)."""
        self.restart_requested = self.restart_requested or restart
        if self._loop and self._quit and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._quit.set)
        if self.on_quit:
            self.on_quit()

    def run_coro(self, coro):
        """Schedule a coroutine on the app loop from another thread."""
        if not self._loop:
            raise RuntimeError("app loop not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ------------------------------------------------------------------ settings
    def sync_autostart(self) -> None:
        try:
            want = self.settings.app.start_with_windows
            have = get_autostart()
            if want and have != launch_command():
                set_autostart(True)
            elif not want:
                set_autostart(False)      # idempotent; also clears an entry from before the rename
        except OSError as exc:
            logger.warning("could not update the Run key: %s", exc)

    async def commit_settings(self, new: Settings) -> list[str]:
        """Persist (off-loop), apply live, and return dotted names of changed restart-only
        fields. Must be awaited on the app loop."""
        changed = changed_paths(self.settings, new)
        await asyncio.to_thread(save_settings, new)
        self.settings = new
        restart = [".".join(p) for p in changed if p in RESTART_FIELDS]
        self.restart_pending.update(restart)
        logging.getLogger().setLevel(new.app.log_level)
        if ("app", "start_with_windows") in changed:
            await asyncio.to_thread(self.sync_autostart)
        self.pipeline.apply_settings(new)
        if self.on_state_change:
            self.on_state_change()
        logger.info("settings saved (%d changed%s)", len(changed),
                    f", restart needed for {', '.join(restart)}" if restart else "")
        return restart

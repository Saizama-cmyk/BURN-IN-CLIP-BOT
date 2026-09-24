"""The Windows desktop shell: native window (pywebview / WebView2), tray icon (pystray),
single-instance lock, and restart/quit handling.

Threads: pywebview owns the main thread; the Ashvane engine (asyncio loop with the pipeline
and the dashboard server) runs on a worker thread; pystray runs detached on its own thread.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from .app import ClipBotApp
from .config import APP_NAME, FULL_NAME, Settings, resource_path
from .util import kill_all_sync

logger = logging.getLogger("clipbot.desktop")

MUTEX_NAME = "Local\\Ashvane.SingleInstance"
ERROR_ALREADY_EXISTS = 183
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
TRAY_TIP_MAX = 127          # Windows NOTIFYICONDATA.szTip holds 128 WCHARs
ENGINE_WAIT_FACTOR = 4      # engine stop may take several child-process grace periods


# --------------------------------------------------------------------------- single instance
class SingleInstance:
    def __init__(self) -> None:
        self.handle = None
        self.already_running = False
        if os.name != "nt":
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        self.handle = k32.CreateMutexW(None, False, MUTEX_NAME)
        self.already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if self.handle and os.name == "nt":
            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None


def focus_existing(settings: Settings) -> bool:
    """Ask the running instance to show its window."""
    d = settings.dashboard
    try:
        r = httpx.post(f"http://{d.host}:{d.port}/api/control/focus", headers={"X-ClipBot": "1"},
                       timeout=settings.discovery.http_timeout_s)
        return r.status_code == 200
    except httpx.HTTPError as exc:
        logger.warning("another Ashvane is running but did not answer: %s", exc)
        return False


def message_box(text: str, title: str = APP_NAME) -> None:
    if os.name == "nt":
        MB_ICONWARNING = 0x30
        ctypes.windll.user32.MessageBoxW(None, text, title, MB_ICONWARNING)
    else:
        logger.error("%s: %s", title, text)


# --------------------------------------------------------------------------- relaunch
def relaunch_command() -> tuple[list[str], str | None]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *sys.argv[1:]], None
    root = str(Path(__file__).resolve().parent.parent)
    return [sys.executable, "-m", "clipbot", *sys.argv[1:]], root


def relaunch(detached: bool) -> None:
    args, cwd = relaunch_command()
    flags = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP) if detached else 0
    for extra in (CREATE_BREAKAWAY_FROM_JOB, 0):
        try:
            subprocess.Popen(args, cwd=cwd, creationflags=flags | extra, close_fds=True)
            logger.info("relaunched: %s", " ".join(args))
            return
        except OSError as exc:
            logger.debug("relaunch with flags %#x failed: %s", flags | extra, exc)
    logger.error("could not relaunch Ashvane; start it again manually")


# --------------------------------------------------------------------------- desktop pet
RECT_LONGS = 4                             # RECT = left, top, right, bottom
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080              # keeps the pet out of the taskbar and Alt+Tab
WS_EX_APPWINDOW = 0x00040000
PET_KEY_ARGB = (255, 1, 1, 1)               # form colour made see-through behind the pet
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020            # the overlay never takes a click; PetInput does the work
LWA_COLORKEY = 0x1                         # keyed pixels vanish *and* stop taking mouse input
LWA_ALPHA = 0x2
LAYER_OPAQUE = 255
# Windows 11 paints its system backdrop (#202020 in dark mode) under keyed pixels of a window that
# hosts WebView2 (runtime 153+), turning the see-through overlay into a grey sheet. Switch it off.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE = 1
VK_LBUTTON = 0x01
KEY_DOWN = 0x8000
MS = 1000.0
CARD_HOVER_MS = 50.0                       # ms between card hover syncs
GREEN_SHIFT = 8                             # COLORREF is 0x00BBGGRR
BLUE_SHIFT = 16
SPI_GETWORKAREA = 0x0030
SM_CYSCREEN = 1
TASKBAR_SPAN = 0.8        # a bar this wide runs along the top or bottom edge
FALLBACK_AREA = {"x": 0, "y": 0, "w": 1920, "h": 1040}   # non-Windows dev only


def taskbar_rect() -> tuple[int, int, int, int] | None:
    """Where the taskbar is, even when it auto-hides (Windows leaves it out of the work area then,
    so a pet standing on the work-area floor gets its feet covered)."""
    hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
    if not hwnd:
        return None
    r = (ctypes.c_long * RECT_LONGS)()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return tuple(r)


def work_area() -> dict:
    """Primary monitor's work area (screen minus taskbar) in physical pixels."""
    if os.name != "nt":
        return dict(FALLBACK_AREA)
    rect = (ctypes.c_long * RECT_LONGS)()
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    x0, y0, x1, y1 = rect
    bar = taskbar_rect()
    if bar:
        bx0, by0, bx1, by1 = bar
        wide = bx1 - bx0 >= (x1 - x0) * TASKBAR_SPAN        # a bottom/top bar, not a side one
        bar_h = by1 - by0
        screen_h = ctypes.windll.user32.GetSystemMetrics(SM_CYSCREEN)
        if wide and by0 >= screen_h - bar_h:                # bottom bar (hidden ones sit off-screen)
            y1 = min(y1, screen_h - bar_h)
        elif wide and by1 <= bar_h:                         # top bar
            y0 = max(y0, bar_h)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


SWP_NOSIZE = 0x1
SWP_NOMOVE = 0x2
SWP_NOZORDER = 0x4
SWP_NOACTIVATE = 0x10


class PetInput(threading.Thread):
    """Mouse for the pet.

    A colour-keyed window gets no mouse messages at all (Windows routes them to whatever is behind
    it), so hovering and dragging happen here: watch the cursor and the left button, move the
    window ourselves, and tell the page what happened. While the hover card is open the window is
    opaque, so the page takes its own clicks and this stays out of the way."""

    def __init__(self, desktop: "Desktop") -> None:
        super().__init__(name="pet-input", daemon=True)
        self.desktop = desktop
        self.stop = threading.Event()
        self.card = False
        self._in = False
        self._hovering = False
        self._since = 0.0
        self._left = 0.0
        self.rect: tuple[int, int, int, int] | None = None
        self._drag: dict | None = None
        self._last_click = 0.0
        self._at = (-1, -1)
        self._synced = 0.0
        self._card_down = False
        self._healed = 0.0

    def run(self) -> None:
        if os.name != "nt":
            return
        u32 = ctypes.windll.user32
        pt = ctypes.wintypes.POINT()
        while not self.stop.wait(self.desktop.app.settings.pets.input_poll_ms / MS):
            if self.desktop.pet is None or not self.desktop.pet_visible:
                continue
            try:
                now = time.monotonic()
                if now - self._healed >= self.desktop.app.settings.pets.heal_s:
                    self._healed = now
                    self.desktop.pet_heal()
                u32.GetCursorPos(ctypes.byref(pt))
                if self.rect:
                    self._tick(pt.x, pt.y, bool(u32.GetAsyncKeyState(VK_LBUTTON) & KEY_DOWN))
            except OSError as exc:
                logger.debug("pet input: %s", exc)

    def _tick(self, x: int, y: int, down: bool) -> None:
        p = self.desktop.app.settings.pets
        left, top, w, h = self.rect
        inside = left <= x < left + w and top <= y < top + h
        now = time.monotonic()

        if self._drag is not None:
            self._move(x, y, down, p)
            return
        if self.card and inside:
            self._card_mouse(x, y, down, now)
            return
        if down and inside:
            self._drag = {"x": x, "y": y, "moved": False}
            self.desktop.pet_js(f"PetIn.grab({x},{y})")
            return

        if inside != self._in:
            self._in = inside
            self._since = self._left = now
        if inside and not self._hovering and now - self._since >= p.hover_ms / MS:
            self._hovering = True
            self.desktop.pet_js("PetIn.enter()")
        elif not inside and self._hovering and now - self._left >= p.leave_ms / MS:
            self._hovering = False
            self.desktop.pet_js("PetIn.leave()")

    def _card_mouse(self, x: int, y: int, down: bool, now: float) -> None:
        """Mouse over the open card: keep its buttons lit, and pass a click to the page."""
        if (x, y) != self._at and now - self._synced >= CARD_HOVER_MS / MS:
            self._at, self._synced = (x, y), now
            self.desktop.pet_js(f"PetIn.at({x},{y})")
        if down and not self._card_down:
            self.desktop.pet_js(f"PetIn.click({x},{y})")
        self._card_down = down

    def _move(self, x: int, y: int, down: bool, p) -> None:
        d = self._drag
        if abs(x - d["x"]) + abs(y - d["y"]) > p.drag_slop_px:
            d["moved"] = True
        if d["moved"]:
            self.desktop.pet_js(f"PetIn.drag({x},{y})")
        if down:
            return
        self._drag = None
        self.desktop.pet_js("PetIn.drop(%s)" % ("true" if d["moved"] else "false"))
        if d["moved"]:
            return
        now = time.monotonic()
        if now - self._last_click <= p.double_click_ms / MS:
            self._last_click = 0.0
            self.desktop.show_window()
        else:
            self._last_click = now


class PetBridge:
    """Python side of the pet window (window.pywebview.api.* in static/pet.html).

    Everything here is in physical pixels and goes straight to Win32, so display scaling
    (125 %, 150 %…) can't push the pet off-screen; the page converts with devicePixelRatio."""

    def __init__(self, desktop: "Desktop") -> None:
        self._desktop = desktop

    def _hwnd(self) -> int | None:
        pet = self._desktop.pet
        try:
            return pet.native.Handle.ToInt32() if pet is not None else None
        except AttributeError:
            return None

    def _set(self, x=0, y=0, w=0, h=0, flags=0) -> None:
        hwnd = self._hwnd()
        if hwnd:
            ctypes.windll.user32.SetWindowPos(hwnd, None, int(x), int(y), int(w), int(h),
                                              flags | SWP_NOZORDER | SWP_NOACTIVATE)

    def area(self) -> dict:
        return work_area()

    def move(self, x: float, y: float) -> None:
        self._set(x, y, flags=SWP_NOSIZE)

    def resize(self, w: float, h: float) -> None:
        self._set(w=w, h=h, flags=SWP_NOMOVE)

    def place(self, x: float, y: float, w: float, h: float) -> None:
        self._set(x, y, w, h)

    def where(self) -> dict:
        hwnd = self._hwnd()
        if not hwnd:
            return {}
        r = (ctypes.c_long * RECT_LONGS)()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        left, top, right, bottom = r
        return {"x": left, "y": top, "w": right - left, "h": bottom - top}

    def open_app(self) -> None:
        self._desktop.show_window()

    def hit(self, x: float, y: float, w: float, h: float) -> None:
        """Where the pet (plus its open card) is, in page pixels; converted to screen here."""
        ox, oy = self._client_origin()
        self._desktop.set_pet_hit((int(x) + ox, int(y) + oy, int(w), int(h)))

    def _client_origin(self) -> tuple[int, int]:
        """Screen position of the page's top-left corner (the client area, not the window)."""
        hwnd = self._desktop.pet_hwnd()
        if not hwnd:
            return (0, 0)
        pt = ctypes.wintypes.POINT(0, 0)
        if not ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return (0, 0)
        return (pt.x, pt.y)

    def interactive(self, on: bool) -> None:
        """The page opened or closed its hover card; the window stays see-through either way."""
        self._desktop.set_pet_card(bool(on))

    def toggle_pause(self) -> None:
        self._desktop._toggle_pause()

    def hide(self) -> None:
        self._desktop.set_pet_visible(False)


def no_backdrop(hwnd: int) -> None:
    """No Windows 11 backdrop and no dark frame fill: keyed pixels show the desktop again."""
    dwm = ctypes.windll.dwmapi
    for attr, value in ((DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_NONE), (DWMWA_USE_IMMERSIVE_DARK_MODE, 0)):
        v = ctypes.c_int(value)
        hr = dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
        if hr:  # older Windows builds don't know the backdrop attribute; nothing to undo there
            logger.debug("pet DwmSetWindowAttribute(%d) -> %#x", attr, ctypes.c_uint32(hr).value)


# --------------------------------------------------------------------------- identity
APP_USER_MODEL_ID = "Ashvane.App"         # taskbar grouping + icon, even when run from python
WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG = 0, 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
ICON_SIZES = ((ICON_SMALL, 16), (ICON_BIG, 32))


def claim_app_identity() -> None:
    """Tell Windows this process is Ashvane (not python.exe) for the taskbar."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError) as exc:
        logger.debug("app id: %s", exc)


def set_window_icon(window) -> None:
    """Give a pywebview window the Ashvane icon (title bar, taskbar, Alt+Tab)."""
    ico = resource_path("assets", "clipbot.ico")
    if os.name != "nt" or window is None or not ico.exists():
        return
    try:
        hwnd = window.native.Handle.ToInt32()
        u32 = ctypes.windll.user32
        u32.LoadImageW.restype = ctypes.c_void_p
        for which, px in ICON_SIZES:
            h = u32.LoadImageW(None, str(ico), IMAGE_ICON, px, px, LR_LOADFROMFILE)
            if h:
                u32.SendMessageW(hwnd, WM_SETICON, which, ctypes.c_void_p(h))
    except (AttributeError, OSError) as exc:
        logger.debug("window icon: %s", exc)


# --------------------------------------------------------------------------- tray
def tray_image():
    from PIL import Image

    ico = resource_path("assets", "clipbot.ico")
    if ico.exists():
        return Image.open(ico)
    from .icon import make_icon

    return make_icon()


def status_line(app: ClipBotApp) -> str:
    p = app.pipeline
    state = "paused" if p.manual_paused else "FAILSAFE" if p.failsafe_paused else "running"
    return (f"{state} · {len(p.targets)} monitors · {p.in_flight} in queue · "
            f"pressure {p.pressure:.0%}")


class Desktop:
    def __init__(self, settings: Settings, start_hidden: bool) -> None:
        self.settings = settings
        self.app = ClipBotApp(settings)
        self.start_hidden = start_hidden
        self.window = None
        self.pet = None
        self.pet_visible = False
        self._pet_input: PetInput | None = None
        self._pet_lock = threading.Lock()
        self._pet_reset_seen = ""
        self.icon = None
        self.quitting = False
        self._engine: threading.Thread | None = None
        self._tray_stop = threading.Event()
        self._quit_lock = threading.Lock()
        self._quit_started = False

    # engine ----------------------------------------------------------------
    def _run_engine(self) -> None:
        try:
            asyncio.run(self.app.run())
        except Exception:  # the engine thread must never die silently
            logger.exception("engine crashed")
            self.app.error = self.app.error or "the Ashvane engine crashed (see log)"
        finally:
            self.app.ready.set()
            self.app.stopped.set()
            if not self.quitting:
                self.quit()

    # window ----------------------------------------------------------------
    def show_window(self) -> None:
        if self.window is not None:
            self.window.show()
            self.window.restore()

    def _on_closing(self) -> bool:
        if self.quitting or not self.app.settings.app.close_to_tray:
            threading.Thread(target=self.quit, name="quit", daemon=True).start()
            return True
        self.window.hide()
        return False

    # pet -------------------------------------------------------------------
    def _create_pet(self, webview) -> None:
        p = self.app.settings.pets
        if not p.enabled:
            return
        area = work_area()
        self.pet = webview.create_window(
            "Ashvane pet", f"{self.app.url.rstrip('/')}/pet?k={self.app.pet_token}",
            width=area["w"], height=area["h"], x=area["x"], y=area["y"],
            frameless=True, transparent=True, on_top=p.always_on_top, resizable=False,
            focus=False, shadow=False, js_api=PetBridge(self))
        self.pet.events.shown += self._pet_shown
        self.pet_visible = True

    def _pet_shown(self) -> None:
        """Tool-window style, colour-key transparency, and our own mouse tracking."""
        hwnd = self.pet_hwnd()
        if hwnd:
            self._pet_style(hwnd)
        self._pet_backcolor()
        self.pet_keyed(True)
        if self._pet_input is None:
            self._pet_input = PetInput(self)
            self._pet_input.start()

    def _pet_style(self, hwnd: int) -> None:
        u32 = ctypes.windll.user32
        ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                           (ex | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TRANSPARENT)
                           & ~WS_EX_APPWINDOW)
        no_backdrop(hwnd)

    def pet_heal(self) -> None:
        """Put the see-through, click-through overlay back if it was lost mid-session (Windows
        re-applying its backdrop, or the window being rebuilt without our styles). Without this a
        reset leaves a full-screen sheet over the desktop until Ashvane restarts."""
        hwnd = self.pet_hwnd()
        if not hwnd:
            return
        u32 = ctypes.windll.user32
        ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        key, alpha, flags = ctypes.c_uint(), ctypes.c_ubyte(), ctypes.c_uint()
        u32.GetLayeredWindowAttributes(hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags))
        backdrop = ctypes.c_int()
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(backdrop), ctypes.sizeof(backdrop))
        styled = bool(ex & WS_EX_LAYERED and ex & WS_EX_TRANSPARENT and flags.value & LWA_COLORKEY)
        backdrop_ok = hr != 0 or backdrop.value == DWMSBT_NONE     # hr: older Windows, no backdrop
        if styled and backdrop_ok:
            return
        state = f"ex={ex:#x} layered-flags={flags.value} backdrop={backdrop.value}"
        if state != self._pet_reset_seen:          # say it once per kind of reset, not every second
            self._pet_reset_seen = state
            logger.warning("pet overlay was reset (%s); repairing it", state)
        self._pet_style(hwnd)
        self.pet_keyed(True)

    def pet_hwnd(self) -> int | None:
        try:
            return self.pet.native.Handle.ToInt32() if self.pet is not None else None
        except (AttributeError, OSError) as exc:
            logger.debug("pet handle: %s", exc)
            return None

    def _pet_backcolor(self) -> None:
        """Paint the form in the key colour (WebView2 draws nothing where the page is clear)."""
        try:
            import clr  # noqa: F401  (pythonnet, loaded by pywebview on Windows)
            from System import Action
            from System.Drawing import Color

            form = self.pet.native
            form.Invoke(Action(lambda: setattr(form, "BackColor", Color.FromArgb(*PET_KEY_ARGB))))
        except Exception as exc:  # noqa: BLE001  pythonnet/WinForms raise .NET exceptions
            logger.warning("pet window transparency unavailable: %s", exc)

    def pet_keyed(self, keyed: bool) -> None:
        """See-through (and click-through), or opaque and clickable while the card is open."""
        hwnd = self.pet_hwnd()
        if not hwnd:
            return
        _a, r, g, b = PET_KEY_ARGB
        key = r | (g << GREEN_SHIFT) | (b << BLUE_SHIFT)
        if not ctypes.windll.user32.SetLayeredWindowAttributes(
                hwnd, key, LAYER_OPAQUE, LWA_COLORKEY if keyed else LWA_ALPHA):
            logger.warning("pet transparency: SetLayeredWindowAttributes failed")

    def set_pet_card(self, open_: bool) -> None:
        """The page opened or closed its hover card."""
        if self._pet_input is not None:
            self._pet_input.card = open_

    def set_pet_hit(self, rect: tuple[int, int, int, int]) -> None:
        if self._pet_input is not None:
            self._pet_input.rect = rect

    def pet_js(self, code: str) -> None:
        pet = self.pet
        if pet is None:
            return
        try:
            pet.evaluate_js(code)
        except (OSError, RuntimeError, KeyError, AttributeError, ValueError) as exc:
            logger.debug("pet js (%s): %s", code, exc)

    def _settings_changed(self) -> None:
        """Settings were saved: bring the pet up or down live (no restart needed)."""
        threading.Thread(target=self._sync_pet, name="pet-sync", daemon=True).start()

    def _sync_pet(self) -> None:
        import webview

        with self._pet_lock:
            p = self.app.settings.pets
            if self.quitting:
                return
            if p.enabled and self.pet is None:
                self._create_pet(webview)
                logger.info("desktop pet on")
            elif not p.enabled and self.pet is not None:
                pet, self.pet, self.pet_visible = self.pet, None, False
                try:
                    pet.destroy()
                except (OSError, RuntimeError, KeyError) as exc:
                    logger.debug("pet destroy: %s", exc)
                logger.info("desktop pet off")
            elif self.pet is not None and self.pet.on_top != p.always_on_top:
                self.pet.on_top = p.always_on_top

    def set_pet_visible(self, visible: bool) -> None:
        if self.pet is None:
            return
        (self.pet.show if visible else self.pet.hide)()
        self.pet_visible = visible

    # tray ------------------------------------------------------------------
    def _toggle_pause(self, *_):
        p = self.app.pipeline
        coro = p.resume() if p.manual_paused else p.pause()
        self.app.run_coro(coro)

    def _open_clips(self, *_):
        folder = self.app.paths.clips
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))  # noqa: S606 — Explorer on our own clips folder

    def _build_tray(self):
        import pystray

        item = pystray.MenuItem
        menu = pystray.Menu(
            item("Open Ashvane", lambda *_: self.show_window(), default=True),
            item(lambda _i: status_line(self.app), None, enabled=False),
            item(lambda _i: "Resume capture" if self.app.pipeline.manual_paused else "Pause capture",
                 self._toggle_pause),
            item("Open clips folder", self._open_clips),
            item(lambda _i: "Hide desktop pet" if self.pet_visible else "Show desktop pet",
                 lambda *_: self.set_pet_visible(not self.pet_visible),
                 visible=lambda _i: self.pet is not None),
            pystray.Menu.SEPARATOR,
            item("Quit", lambda *_: threading.Thread(target=self.quit, daemon=True).start()),
        )
        self.icon = pystray.Icon(APP_NAME, tray_image(), f"{APP_NAME} — starting", menu)
        self.icon.run_detached()
        threading.Thread(target=self._tray_refresh, name="tray-refresh", daemon=True).start()

    def _tray_refresh(self) -> None:
        while not self._tray_stop.wait(self.app.settings.dashboard.refresh_ms / 1000):
            if self.icon is None:
                continue
            try:
                self.icon.title = f"{APP_NAME} — {status_line(self.app)}"[:TRAY_TIP_MAX]
                self.icon.update_menu()
            except (OSError, RuntimeError, AttributeError) as exc:
                logger.debug("tray refresh: %s", exc)

    # lifecycle -------------------------------------------------------------
    def quit(self) -> None:
        with self._quit_lock:
            if self._quit_started:
                return
            self._quit_started = True
        self.quitting = True
        self.app.request_quit()
        self.app.stopped.wait(self.app.settings.app.shutdown_timeout_s * ENGINE_WAIT_FACTOR)
        kill_all_sync()
        self._tray_stop.set()
        if self.icon is not None:
            try:
                self.icon.stop()
            except (OSError, RuntimeError) as exc:
                logger.debug("tray stop: %s", exc)
        if self.pet is not None:
            try:
                self.pet.destroy()
            except (OSError, RuntimeError, KeyError) as exc:
                logger.debug("pet destroy: %s", exc)
        if self.window is not None:
            try:
                self.window.destroy()
            except (OSError, RuntimeError, KeyError) as exc:
                logger.debug("window destroy: %s", exc)

    def run(self) -> int:
        import webview

        claim_app_identity()
        self.app.on_focus = self.show_window
        self.app.on_state_change = self._settings_changed

        def pick_folder() -> str | None:
            if self.window is None:
                return None
            dialog = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or webview.FOLDER_DIALOG
            picked = self.window.create_file_dialog(dialog)
            return picked[0] if picked else None
        self.app.pick_folder = pick_folder
        self.app.on_quit = None
        self._engine = threading.Thread(target=self._run_engine, name="engine", daemon=True)
        self._engine.start()
        self.app.ready.wait()
        if self.app.error and not self.app.pipeline.running:
            message_box(f"Ashvane could not start:\n\n{self.app.error}")
            self.quit()
            return 1
        self._build_tray()
        d, a = self.app.settings.dashboard, self.app.settings.app
        # a launch you start yourself always opens the window; only a sign-in start may stay in the tray
        hidden = self.start_hidden or not a.open_window_on_launch
        area = work_area()                      # centred on the screen, never hanging off an edge
        width, height = min(d.window_width, area["w"]), min(d.window_height, area["h"])
        self.window = webview.create_window(
            FULL_NAME, self.app.url, width=width, height=height,
            x=area["x"] + (area["w"] - width) // 2, y=area["y"] + (area["h"] - height) // 2,
            min_size=(d.window_min_width, d.window_min_height), hidden=hidden, background_color="#0F1B2D", text_select=True)
        self.window.events.closing += self._on_closing
        self.window.events.shown += lambda: set_window_icon(self.window)
        self._create_pet(webview)
        webview.start(private_mode=False, storage_path=str(self.app.paths.data / "webview"))
        # the GUI loop returned: every window is gone
        if not self.quitting:
            self.quit()
        if self._engine:
            self._engine.join(self.app.settings.app.shutdown_timeout_s * ENGINE_WAIT_FACTOR)
        return 0

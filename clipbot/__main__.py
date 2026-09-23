"""Ashvane entry point.

    python -m clipbot                 console dev mode: engine + dashboard at 127.0.0.1:8787
    python -m clipbot --window        desktop app (native window + tray)
    python -m clipbot --headless      engine + dashboard, no window/tray (servers, tests)
    python -m clipbot --no-dashboard  engine only
    ClipBot.exe                       the installed desktop app (same as --window)
    ClipBot.exe --streamlink ARGS     internal: run the bundled streamlink (capture subprocess)
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # launched as a script path (e.g. from the Run key in dev)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "clipbot"

import argparse
import asyncio
import io
import logging
import os
import webbrowser

from clipbot.config import ConfigError, is_frozen, load_settings, migrate_data_dir, settings_path

logger = logging.getLogger("clipbot.main")

STD_OUTPUT_HANDLE = -11   # Win32 GetStdHandle ids
STD_ERROR_HANDLE = -12


def _ensure_std_streams() -> None:
    """A windowed exe may start with sys.stdout/stderr = None even when the parent handed it
    pipes. Rebuild them from the inherited OS handles so streamlink --stdout works."""
    if os.name != "nt":
        return
    import ctypes
    import msvcrt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetStdHandle.restype = ctypes.c_void_p
    invalid = ctypes.c_void_p(-1).value
    for name, std_id, mode in (("stdout", STD_OUTPUT_HANDLE, "wb"), ("stderr", STD_ERROR_HANDLE, "wb")):
        stream = getattr(sys, name)
        if stream is not None:
            try:
                stream.fileno()
                continue
            except (OSError, ValueError, io.UnsupportedOperation):
                pass
        handle = k32.GetStdHandle(std_id)
        if not handle or handle == invalid:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            continue
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
        raw = open(fd, mode, buffering=0, closefd=False)
        setattr(sys, name, io.TextIOWrapper(io.BufferedWriter(raw), encoding="utf-8",
                                            line_buffering=True, write_through=True))


def run_streamlink(argv: list[str]) -> int:
    _ensure_std_streams()
    from streamlink_cli.main import main as streamlink_main

    sys.argv = ["streamlink", *argv]
    try:
        streamlink_main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="clipbot", description="Ashvane")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--window", action="store_true", help="desktop app with window and tray")
    mode.add_argument("--headless", action="store_true", help="engine + dashboard, no window")
    mode.add_argument("--console", action="store_true", help="console dev mode (default in dev)")
    p.add_argument("--no-dashboard", action="store_true", help="engine only, no web dashboard")
    p.add_argument("--minimized", action="store_true", help="start hidden in the tray")
    p.add_argument("--autostart", action="store_true", help="launched by Windows sign-in")
    return p.parse_args(argv)


def autostart_hidden(args: argparse.Namespace, settings) -> bool:
    """Start in the tray only? A sign-in launch is silent unless the user turned that off."""
    return bool(args.minimized or (args.autostart and (settings.app.autostart_silent
                                                        or settings.app.start_minimized)))


def _fatal(msg: str, windowed: bool) -> int:
    logger.error(msg)
    if windowed:
        from clipbot.desktop import message_box

        message_box(msg)
    elif sys.stderr is not None:
        sys.stderr.write(msg + "\n")
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--streamlink"]:
        return run_streamlink(argv[1:])

    args = parse_args(argv)
    windowed = args.window or (is_frozen() and not (args.headless or args.console
                                                     or args.no_dashboard))
    moved = migrate_data_dir()
    try:
        settings = load_settings()
    except ConfigError as exc:
        return _fatal(f"Ashvane could not read {settings_path()}:\n{exc}\n\n"
                      "Fix or delete the file and start Ashvane again.", windowed)

    from clipbot.app import ClipBotApp, setup_logging

    log_file = setup_logging(settings, console=not windowed)
    logger.info("Ashvane starting (%s mode, settings %s, log %s)",
                "window" if windowed else "headless" if args.headless else "console",
                settings_path(), log_file)
    if moved:
        logger.info("moved your data to %s (the app's new name)", moved)

    from clipbot.desktop import Desktop, SingleInstance, focus_existing, relaunch

    lock = SingleInstance() if settings.app.single_instance else None
    if lock and lock.already_running:
        logger.info("Ashvane is already running; focusing it")
        if not focus_existing(settings) and windowed:
            return _fatal("Ashvane is already running (check the tray).", windowed)
        return 0

    if windowed:
        desktop = Desktop(settings, start_hidden=autostart_hidden(args, settings))
        code = desktop.run()
        restart = desktop.app.restart_requested
    else:
        app = ClipBotApp(settings, dashboard=not args.no_dashboard)
        if settings.dashboard.open_browser and not args.headless and not args.no_dashboard:
            def _open() -> None:
                app.ready.wait()
                if not app.error:
                    webbrowser.open(app.url)
            import threading

            threading.Thread(target=_open, daemon=True).start()
        try:
            asyncio.run(app.run())
        except KeyboardInterrupt:
            logger.info("interrupted; shut down cleanly")
        code = 1 if app.error else 0
        restart = app.restart_requested
        if app.error and sys.stderr is not None:
            sys.stderr.write(app.error + "\n")

    if lock:
        lock.release()
    if restart:
        relaunch(detached=windowed)
    logging.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())

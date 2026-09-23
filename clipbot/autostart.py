""""Start with Windows": the HKCU ...\\CurrentVersion\\Run value for the app."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .config import LEGACY_RUN_VALUES, RUN_VALUE_NAME, is_frozen

logger = logging.getLogger("clipbot.autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_FLAG = "--autostart"


def launch_command() -> str:
    """Command line that starts the desktop app (frozen exe, or pythonw in dev)."""
    if is_frozen():
        return f'"{sys.executable}" {AUTOSTART_FLAG}'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    main = Path(__file__).resolve().parent / "__main__.py"
    return f'"{runner}" "{main}" --window {AUTOSTART_FLAG}'


def get_autostart(name: str = RUN_VALUE_NAME) -> str | None:
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except FileNotFoundError:
        return None


def set_autostart(enabled: bool, name: str = RUN_VALUE_NAME, command: str | None = None) -> None:
    """Add or remove the Run value. Idempotent."""
    if os.name != "nt":
        logger.warning("start with Windows is only supported on Windows")
        return
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if name == RUN_VALUE_NAME:
            for legacy in LEGACY_RUN_VALUES:      # an entry from before the rename
                try:
                    winreg.DeleteValue(key, legacy)
                except FileNotFoundError:
                    pass
        if enabled:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command or launch_command())
            logger.info("start with Windows enabled")
        else:
            try:
                winreg.DeleteValue(key, name)
                logger.info("start with Windows disabled")
            except FileNotFoundError:
                pass

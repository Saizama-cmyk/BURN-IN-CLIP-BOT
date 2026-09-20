"""Shared helpers for the GATES.md scripts: start ClipBot in a throwaway data folder,
wait for the dashboard, and shut it down through the API."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
BASE = "http://127.0.0.1:8787"
H = {"X-ClipBot": "1"}
BOOT_TIMEOUT_S = 60
EXIT_TIMEOUT_S = 60


def fresh_home(tag: str) -> Path:
    home = Path(tempfile.mkdtemp(prefix=f"clipbot-gate-{tag}-"))
    return home


GATE_PASSWORD = "gate-password-1"
CLIENT = httpx.Client(base_url=BASE, timeout=60)


def login() -> httpx.Client:
    """Sign the shared gate client in (creating the profile on a fresh home)."""
    st = CLIENT.get("/api/auth/status").json()
    if st["setup"]:
        CLIENT.post("/api/auth/setup", headers=H, json={"name": "Gate", "password": GATE_PASSWORD})
    else:
        CLIENT.post("/api/auth/login", headers=H, json={"password": GATE_PASSWORD})
    return CLIENT


def start(cmd: list[str], home: Path) -> subprocess.Popen:
    env = dict(os.environ, CLIPBOT_HOME=str(home))
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    deadline = time.time() + BOOT_TIMEOUT_S
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"FAILED: app exited early with {proc.returncode}")
        try:
            if httpx.get(f"{BASE}/api/auth/status", timeout=2).status_code == 200:
                login()
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    proc.kill()
    raise SystemExit("FAILED: dashboard did not come up")


def start_dev(home: Path) -> subprocess.Popen:
    return start([PY, "-m", "clipbot", "--headless"], home)


def stop(proc: subprocess.Popen) -> int:
    t0 = time.time()
    try:
        r = CLIENT.post("/api/control/quit", headers=H, timeout=EXIT_TIMEOUT_S)
        note = f"quit HTTP {r.status_code} after {time.time() - t0:.1f}s"
    except httpx.HTTPError as exc:
        note = f"quit request failed after {time.time() - t0:.1f}s: {type(exc).__name__} {exc}"
    sys.stderr.write(note + "\n")
    try:
        return proc.wait(EXIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1


def out(line: str) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(line + "\n")
    sys.stdout.flush()

"""Test the phone connection from the PC: every address a phone could use, tried for real.

The phone reaches the PC one of three ways: this PC's address on the home Wi-Fi, its Tailscale
address, or its Tailscale name (served over HTTPS by ``tailscale serve``). Each is fetched here
exactly the way the phone app fetches it, so "works" means the phone's request would be answered
too, and a failure comes with the one thing to do about it. Nothing is changed; this only looks.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

import httpx

from .util import CmdTimeout, run_cmd

logger = logging.getLogger("clipbot.remote_check")

STATUS_PATH = "/api/auth/status"
ACTION_HEADER = {"X-ClipBot": "1"}
TAILSCALE_DEFAULT = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"


def _tailscale() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    return str(TAILSCALE_DEFAULT) if TAILSCALE_DEFAULT.exists() else None


async def _tailnet(timeout_s: float) -> tuple[str, str, bool]:
    """(Tailscale IP, Tailscale name, whether `tailscale serve` forwards to this app) or blanks."""
    exe = _tailscale()
    if not exe:
        return "", "", False
    try:
        res = await run_cmd([exe, "status", "--json"], timeout_s)
        me = json.loads(res.out_text or "{}").get("Self") or {}
        ips = [a for a in me.get("TailscaleIPs") or [] if "." in a]
        name = str(me.get("DNSName") or "").rstrip(".")
        serve = await run_cmd([exe, "serve", "status"], timeout_s)
        return (ips[0] if ips else ""), name, "127.0.0.1:" in serve.out_text
    except (CmdTimeout, OSError, ValueError) as exc:
        logger.info("tailscale lookup failed: %s", exc)
        return "", "", False


async def _probe(http: httpx.AsyncClient, url: str, timeout_s: float) -> tuple[bool, str]:
    try:
        r = await http.get(url + STATUS_PATH, headers=ACTION_HEADER, timeout=timeout_s)
    except httpx.HTTPError as exc:
        return False, f"no answer ({type(exc).__name__})"
    if r.status_code == httpx.codes.OK:
        return True, "answers"
    if r.status_code == httpx.codes.FORBIDDEN:
        return False, "refused: Phone remote is off, or it was switched on without a restart"
    return False, f"HTTP {r.status_code}"


async def check(settings, timeout_s: float) -> dict:
    """Try each route and say, in plain words, what the phone should use."""
    d = settings.dashboard
    routes: list[dict] = []
    if not d.remote:
        return {"ok": False, "routes": [],
                "advice": "Phone remote is off. Turn it on in this section, press Save, then "
                          "restart Ashvane."}
    tip, tname, served = await _tailnet(timeout_s)
    async with httpx.AsyncClient(follow_redirects=False) as http:
        from .app import lan_ip       # the same address the app shows as "phone remote at"
        home = lan_ip()
        if home:
            ok, why = await _probe(http, f"http://{home}:{d.port}", timeout_s)
            routes.append({"route": "Home Wi-Fi", "type": home, "ok": ok, "detail": why})
        if tip:
            ok, why = await _probe(http, f"http://{tip}:{d.port}", timeout_s)
            routes.append({"route": "Tailscale address", "type": tip, "ok": ok, "detail": why})
        if tname:
            if served:
                ok, why = await _probe(http, f"https://{tname}", timeout_s)
            else:
                ok, why = False, (f"not set up: run  tailscale serve --bg {d.port}  once on this PC")
            routes.append({"route": "Tailscale name (works anywhere)", "type": tname, "ok": ok,
                           "detail": why})
    good = [r for r in routes if r["ok"]]
    if not good:
        advice = ("Nothing answers yet. Check that Windows Firewall allows Ashvane on private "
                  "networks (run allow-phone-remote.cmd in the install folder), then test again.")
    else:
        best = next((r for r in good if r["route"].startswith("Tailscale name")), good[0])
        advice = f"On the phone, type  {best['type']}  and sign in with your profile password."
        if best["route"] == "Home Wi-Fi":
            advice += " The phone must be on the same Wi-Fi as this PC."
    return {"ok": bool(good), "routes": routes, "advice": advice}

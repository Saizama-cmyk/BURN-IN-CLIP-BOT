"""Local profiles and the dashboard password lock.

* Profiles live in ``<root>/profiles.json``. Each has a name, its own data folder, and a
  password stored only as a salted PBKDF2-HMAC-SHA256 hash (never the password itself).
  The first profile uses the root folder, so data from before profiles existed is kept.
* Sessions are random 256-bit tokens handed to the browser as an HttpOnly, SameSite=Strict
  cookie. Only a SHA-256 of each token is stored (``sessions.json``), so a restart (for
  example after switching profiles) keeps you signed in without keeping usable secrets on disk.
* Wrong passwords are rate-limited per profile (``app.login_max_attempts`` then
  ``app.login_lockout_s``).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import atomic_write, profiles_path, root_dir

logger = logging.getLogger("clipbot.auth")

PBKDF2_ITERATIONS = 600_000     # OWASP 2023+ recommendation for PBKDF2-HMAC-SHA256
SALT_BYTES = 16
TOKEN_BYTES = 32
PROFILE_ID_BYTES = 6
COOKIE = "clipbot_session"
LAST_SEEN_WRITE_S = 30       # record activity at most this often (dashboard polls constantly)
_NAME_RE = re.compile(r"^[\w .'-]{1,40}$", re.UNICODE)


class AuthError(RuntimeError):
    pass


def hash_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass
class Session:
    profile_id: str
    expires: float
    last_seen: float


def password_matches(password: str, prof: dict) -> bool:
    """Does this password open that profile? (Used to keep every login unique.)"""
    got = hash_password(password, bytes.fromhex(prof["salt"]), int(prof["iterations"]))
    return hmac.compare_digest(prof["hash"], got)


def _password_taken(password: str, profiles: list[dict]) -> bool:
    """True when another profile already uses this password.

    Names and passwords both have to be unique, so two accounts on one PC can never share a
    login. Each profile has its own salt, so this is checked by hashing the candidate against
    every stored salt rather than by comparing hashes."""
    return any(password_matches(password, p) for p in profiles)


class ProfileStore:
    """profiles.json + sessions.json, safe to use from several threads."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or root_dir()
        self.path = profiles_path() if root is None else self.root / "profiles.json"
        self.sessions_path = self.root / "sessions.json"
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}

    # ------------------------------------------------------------------ storage
    def _read(self) -> dict:
        if not self.path.exists():
            return {"profiles": [], "last": ""}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        atomic_write(self.path, json.dumps(data, indent=2))

    def profiles(self) -> list[dict]:
        with self._lock:
            return [{"id": p["id"], "name": p["name"]} for p in self._read()["profiles"]]

    def needs_setup(self) -> bool:
        with self._lock:
            return not self._read()["profiles"]

    def last(self) -> str:
        with self._lock:
            return self._read().get("last", "")

    def set_last(self, profile_id: str) -> None:
        with self._lock:
            data = self._read()
            if not any(p["id"] == profile_id for p in data["profiles"]):
                raise AuthError("unknown profile")
            data["last"] = profile_id
            self._write(data)

    # ------------------------------------------------------------------ profiles
    def create(self, name: str, password: str, min_len: int) -> dict:
        name = name.strip()
        if not _NAME_RE.match(name):
            raise AuthError("Name: 1-40 letters, numbers, spaces, . ' -")
        if len(password) < min_len:
            raise AuthError(f"Password must be at least {min_len} characters")
        with self._lock:
            data = self._read()
            if any(p["name"].lower() == name.lower() for p in data["profiles"]):
                raise AuthError("A profile with that name already exists")
            if _password_taken(password, data["profiles"]):
                raise AuthError("That password already belongs to another profile. Every account "
                                "needs its own name and its own password.")
            pid = secrets.token_hex(PROFILE_ID_BYTES)
            salt = secrets.token_bytes(SALT_BYTES)
            first = not data["profiles"]
            prof = {"id": pid, "name": name, "salt": salt.hex(), "iterations": PBKDF2_ITERATIONS,
                    "hash": hash_password(password, salt),
                    # the first profile keeps using the top folder (existing data stays put)
                    "dir": "" if first else f"profiles/{pid}", "created": time.time()}
            data["profiles"].append(prof)
            if first or not data.get("last"):
                data["last"] = pid
            self._write(data)
        (self.root / prof["dir"]).mkdir(parents=True, exist_ok=True)
        logger.info("profile %r created", name)
        return {"id": pid, "name": name}

    def _find(self, data: dict, profile_id: str) -> dict:
        prof = next((p for p in data["profiles"] if p["id"] == profile_id), None)
        if prof is None:
            raise AuthError("unknown profile")
        return prof

    def verify(self, profile_id: str, password: str, max_attempts: int, lockout_s: float) -> bool:
        now = time.time()
        with self._lock:
            fails = [t for t in self._fails.get(profile_id, []) if now - t < lockout_s]
            self._fails[profile_id] = fails
            if len(fails) >= max_attempts:
                wait = int(lockout_s - (now - fails[0])) + 1
                raise AuthError(f"Too many wrong passwords — try again in {wait}s")
            prof = self._find(self._read(), profile_id)
        expected = prof["hash"]
        got = hash_password(password, bytes.fromhex(prof["salt"]), int(prof["iterations"]))
        ok = hmac.compare_digest(expected, got)
        if not ok:
            with self._lock:
                self._fails.setdefault(profile_id, []).append(now)
            logger.warning("wrong password for profile %s", profile_id)
        else:
            with self._lock:
                self._fails.pop(profile_id, None)
        return ok

    def change_password(self, profile_id: str, old: str, new: str, min_len: int,
                        max_attempts: int, lockout_s: float) -> None:
        if len(new) < min_len:
            raise AuthError(f"Password must be at least {min_len} characters")
        if not self.verify(profile_id, old, max_attempts, lockout_s):
            raise AuthError("Current password is wrong")
        others = [p for p in self._read()["profiles"] if p["id"] != profile_id]
        if _password_taken(new, others):
            raise AuthError("That password already belongs to another profile.")
        salt = secrets.token_bytes(SALT_BYTES)
        with self._lock:
            data = self._read()
            prof = self._find(data, profile_id)
            prof.update(salt=salt.hex(), iterations=PBKDF2_ITERATIONS, hash=hash_password(new, salt))
            self._write(data)
        self.revoke_profile(profile_id)

    def rename(self, profile_id: str, name: str) -> None:
        name = name.strip()
        if not _NAME_RE.match(name):
            raise AuthError("Name: 1-40 letters, numbers, spaces, . ' -")
        with self._lock:
            data = self._read()
            self._find(data, profile_id)["name"] = name
            self._write(data)

    # ------------------------------------------------------------------ sessions
    def _read_sessions(self) -> dict:
        if not self.sessions_path.exists():
            return {}
        try:
            return json.loads(self.sessions_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("sessions file corrupt; everyone signs in again")
            return {}

    def create_session(self, profile_id: str, hours: float) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = time.time()
        with self._lock:
            sess = {k: v for k, v in self._read_sessions().items() if v["expires"] > now}
            sess[_token_hash(token)] = {"profile_id": profile_id, "expires": now + hours * 3600,
                                        "last_seen": now}
            atomic_write(self.sessions_path, json.dumps(sess))
        return token

    def check(self, token: str | None, idle_min: float) -> Session | None:
        """Valid session for ``token`` (touches last_seen), else None."""
        if not token:
            return None
        now = time.time()
        key = _token_hash(token)
        with self._lock:
            sess = self._read_sessions()
            s = sess.get(key)
            if s is None or s["expires"] <= now or (idle_min and now - s["last_seen"] > idle_min * 60):
                if s is not None:
                    sess.pop(key)
                    atomic_write(self.sessions_path, json.dumps(sess))
                return None
            if now - s["last_seen"] >= LAST_SEEN_WRITE_S:
                s["last_seen"] = now
                atomic_write(self.sessions_path, json.dumps(sess))
            return Session(s["profile_id"], s["expires"], s["last_seen"])

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            sess = self._read_sessions()
            if sess.pop(_token_hash(token), None) is not None:
                atomic_write(self.sessions_path, json.dumps(sess))

    def revoke_profile(self, profile_id: str | None = None) -> None:
        """Sign everyone out (of one profile, or all)."""
        with self._lock:
            sess = {k: v for k, v in self._read_sessions().items()
                    if profile_id is not None and v["profile_id"] != profile_id}
            atomic_write(self.sessions_path, json.dumps(sess))

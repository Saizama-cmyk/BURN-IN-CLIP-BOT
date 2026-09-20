import json
import time

import pytest

from clipbot import config as C
from clipbot.auth import AuthError, ProfileStore


def test_first_profile_keeps_existing_data(isolated_home):
    s = C.merge_incoming(C.Settings(), {"brand": {"name": "Mine"}})
    C.save_settings(s)                                # settings from before profiles existed
    store = ProfileStore()
    assert store.needs_setup()
    prof = store.create("Sam", "correct horse", 8)
    assert C.home_dir() == isolated_home               # first profile = same folder
    assert C.load_settings().brand.name == "Mine"
    raw = json.loads(C.profiles_path().read_text())
    assert "correct horse" not in C.profiles_path().read_text()
    assert raw["profiles"][0]["iterations"] >= 600_000 and len(raw["profiles"][0]["salt"]) == 32
    second = store.create("Guest", "another pass", 8)
    store.set_last(second["id"])
    assert C.home_dir() == isolated_home / "profiles" / second["id"]
    store.set_last(prof["id"])


def test_verify_and_lockout(isolated_home):
    store = ProfileStore()
    pid = store.create("Sam", "password123", 8)["id"]
    assert store.verify(pid, "password123", 3, 60)
    for _ in range(3):
        assert not store.verify(pid, "nope", 3, 60)
    with pytest.raises(AuthError, match="Too many"):
        store.verify(pid, "password123", 3, 60)       # locked even with the right password


def test_rules(isolated_home):
    store = ProfileStore()
    with pytest.raises(AuthError):
        store.create("Sam", "short", 8)
    store.create("Sam", "longenough", 8)
    with pytest.raises(AuthError):
        store.create("sam", "longenough", 8)          # names are unique, case-insensitive


def test_sessions(isolated_home):
    store = ProfileStore()
    pid = store.create("Sam", "password123", 8)["id"]
    tok = store.create_session(pid, 1)
    assert tok not in store.sessions_path.read_text()   # only a hash is stored
    assert store.check(tok, 30).profile_id == pid
    assert store.check("forged", 30) is None
    store.revoke(tok)
    assert store.check(tok, 30) is None
    t2 = store.create_session(pid, 1)
    store.change_password(pid, "password123", "newpassword1", 8, 5, 60)
    assert store.check(t2, 30) is None                  # password change signs out
    assert store.verify(pid, "newpassword1", 5, 60)


def test_secrets_encrypted_on_disk(isolated_home):
    s = C.merge_incoming(C.Settings(), {"twitch": {"client_secret": "super-secret-value"}})
    C.save_settings(s)
    text = C.settings_path().read_text(encoding="utf-8")
    assert "super-secret-value" not in text and "dpapi:" in text
    assert C.load_settings().twitch.client_secret == "super-secret-value"
    C.save_tokens(isolated_home / "tokens.json", {"youtube": {"access_token": "tok-xyz"}})
    assert "tok-xyz" not in (isolated_home / "tokens.json").read_text()
    assert C.load_tokens(isolated_home / "tokens.json")["youtube"]["access_token"] == "tok-xyz"

"""Boot the real app (engine + dashboard) on a free port and exercise the HTTP API."""
import asyncio
import socket
import threading

import httpx
import pytest

from clipbot import config as C
from clipbot.app import ClipBotApp

H = {"X-ClipBot": "1"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def running_app():
    port = free_port()
    s = C.merge_incoming(C.Settings(), {"dashboard": {"port": port},
                                        "twitch": {"enabled": False}, "kick": {"enabled": False},
                                        "clip_import": {"enabled": False},
                                        "analytics": {"enabled": False}, "ai": {"auto_pull": False}})
    C.save_settings(s)
    app = ClipBotApp(s)
    t = threading.Thread(target=lambda: asyncio.run(app.run()), daemon=True)
    t.start()
    assert app.ready.wait(30) and not app.error, app.error
    base = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=base, timeout=30) as client:
        r = client.post("/api/auth/setup", headers=H, json={"name": "Tester", "password": "pass-word-1"})
        assert r.status_code == 200, r.text
        yield app, client
    app.request_quit()
    assert app.stopped.wait(30)


def test_state_and_index(running_app):
    app, c = running_app
    st = c.get("/api/state").json()
    assert st["status"] in ("idle", "running") and "backlog" in st and "scheduler" in st
    assert c.get("/").status_code == 200 and "BURN-IN" in c.get("/").text


def test_settings_roundtrip_masking(running_app):
    app, c = running_app
    r = c.put("/api/settings", headers=H, json={"settings": {
        "detector": {"z_threshold": 4.25}, "accounts": {"discord_webhook": "https://hook/secret"}}})
    assert r.status_code == 200 and r.json()["ok"]
    got = c.get("/api/settings").json()["settings"]
    assert got["detector"]["z_threshold"] == 4.25
    assert got["accounts"]["discord_webhook"] == C.MASK
    # sending the masked value back must not overwrite the secret
    got["detector"]["z_threshold"] = 5.0
    assert c.put("/api/settings", headers=H, json={"settings": got}).json()["ok"]
    assert app.settings.accounts.discord_webhook == "https://hook/secret"
    assert C.load_settings().accounts.discord_webhook == "https://hook/secret"
    assert app.pipeline.detector.cfg.z_threshold == 5.0          # applied live


def test_validation_errors_are_per_field(running_app):
    _, c = running_app
    r = c.put("/api/settings", headers=H, json={"settings": {"twitch": {"slots": 999}}})
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "twitch.slots"


def test_restart_fields_reported(running_app):
    app, c = running_app
    r = c.put("/api/settings", headers=H, json={"settings": {"workers": {"transcribers": 3}}})
    assert r.json()["restart_required"] == ["workers.transcribers"]
    assert "workers.transcribers" in c.get("/api/state").json()["restart_pending"]


def test_guards(running_app):
    _, c = running_app
    assert c.put("/api/settings", json={}).status_code == 403                 # no action header
    assert c.get("/api/state", headers={"Host": "evil.example"}).status_code == 403


def test_reset_export_import(running_app):
    app, c = running_app
    c.put("/api/settings", headers=H, json={"settings": {"edit": {"crf": 30},
                                                          "kick": {"client_secret": "s3"}}})
    assert c.post("/api/settings/reset/edit", headers=H).json()["settings"]["edit"]["crf"] == C.EditCfg().crf
    exp = c.get("/api/settings/export").json()
    assert exp["kick"]["client_secret"] == ""
    assert c.get("/api/settings/export?include_secrets=true").json()["kick"]["client_secret"] == "s3"
    exp["brand"]["name"] = "Imported"
    r = c.post("/api/settings/import", headers=H, json=exp)
    assert r.json()["ok"] and app.settings.brand.name == "Imported"
    assert app.settings.kick.client_secret == "s3"


def test_schema_and_setup(running_app):
    _, c = running_app
    sc = c.get("/api/settings/schema").json()
    assert sc["leaf_count"] == len(C.LEAF_FIELDS)
    setup = c.get("/api/setup").json()
    ids = {i["id"]: i for i in setup["items"]}
    assert ids["twitch"]["field"] == "twitch.client_id" and not ids["twitch"]["ok"]
    assert setup["ok"] is False


def test_pause_resume(running_app):
    app, c = running_app
    assert c.post("/api/control/pause", headers=H).json()["paused"] is True
    assert c.get("/api/state").json()["status"] == "paused"
    assert c.post("/api/control/resume", headers=H).json()["paused"] is False


def test_media_404_and_bad_id(running_app):
    _, c = running_app
    assert c.get("/media/../../etc").status_code in (400, 404)
    assert c.get("/media/abcdef123456").status_code == 404


def test_lock_blocks_without_session(running_app):
    app, c = running_app
    port = app.settings.dashboard.port
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as anon:
        assert anon.get("/api/state").status_code == 401
        assert anon.get("/").status_code == 200                      # login page itself is public
        assert anon.post("/api/auth/login", headers=H,
                         json={"password": "wrong"}).status_code == 401
        assert anon.post("/api/auth/login", headers=H,
                         json={"password": "pass-word-1"}).json()["ok"]
        assert anon.get("/api/state").status_code == 200
        anon.post("/api/auth/logout", headers=H)
        assert anon.get("/api/state").status_code == 401


def test_oauth_open_uses_app_session_not_browser(running_app, monkeypatch):
    """Connect runs from the signed-in app: the server opens the provider page itself, so the
    external browser never needs a ClipBot session (it used to get {"error":"locked"})."""
    from clipbot.dashboard import server
    opened = []
    monkeypatch.setattr(server.webbrowser, "open", lambda url: opened.append(url) or True)
    app, c = running_app
    port = app.settings.dashboard.port
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as anon:
        assert anon.post("/api/oauth/youtube/open", headers=H).status_code == 401
    r = c.post("/api/oauth/youtube/open", headers=H)
    assert r.status_code == 400 and "client ID" in r.json()["error"]
    c.put("/api/settings", headers=H, json={"settings": {"accounts": {
        "youtube_client_id": "cid.apps.googleusercontent.com", "youtube_client_secret": "sec"}}})
    r = c.post("/api/oauth/youtube/open", headers=H).json()
    assert r["ok"] and opened and opened[0].startswith("https://accounts.google.com/")
    assert "state=" in opened[0] and "redirect_uri=" in opened[0]


def test_pet_endpoints_need_the_pet_key(running_app):
    app, c = running_app
    port = app.settings.dashboard.port
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as anon:
        assert anon.get("/api/pet/state").status_code == 401
        assert anon.get("/api/pet/state?k=wrong").status_code == 401
        r = anon.get(f"/api/pet/state?k={app.pet_token}")
        assert r.status_code == 200 and r.json()["pet"]["species"] == "blip"
        assert anon.get(f"/pet?k={app.pet_token}").status_code == 200
        assert anon.post(f"/api/pet/species?k={app.pet_token}", headers=H,
                         json={"species": "moss"}).json()["species"] == "moss"
        assert anon.post(f"/api/pet/species?k={app.pet_token}", headers=H,
                         json={"species": "dragon"}).status_code == 400
        assert anon.get("/api/state").status_code == 401        # the key opens nothing else


def test_update_install_checks_integrity_and_launches_once(running_app, monkeypatch, tmp_path):
    from clipbot.dashboard import server

    _, client = running_app
    installer = tmp_path / "BURN-IN-Setup.exe"
    launched, options = [], []

    async def check(*args):
        return {"available": True, "latest": "1.1.0", "url": "https://example.test/setup",
                "size": 123, "sha256": "a" * 64}

    async def download(*args, **kwargs):
        options.append(kwargs)
        return installer

    monkeypatch.setattr(server.updater, "check", check)
    monkeypatch.setattr(server.updater, "download", download)
    monkeypatch.setattr(server.updater, "run_installer", launched.append)
    response = client.post("/api/update/install", headers=H)
    assert response.status_code == 200 and response.json()["ok"]
    assert launched == [installer]
    assert options == [{"expected_size": 123, "expected_sha256": "a" * 64}]

    def cannot_launch(path):
        raise OSError("cannot start installer")

    monkeypatch.setattr(server.updater, "run_installer", cannot_launch)
    response = client.post("/api/update/install", headers=H)
    assert response.status_code == 502
    assert "cannot start installer" in response.json()["error"]


def test_private_host_allowed_only_on_our_port():
    """The phone remote widens the rebinding guard to private addresses, nothing else."""
    from clipbot.dashboard.server import _private_host
    assert _private_host("192.168.0.72:8787", 8787)
    assert _private_host("10.1.2.3:8787", 8787)
    assert _private_host("172.16.5.5", 8787)            # no port given
    assert not _private_host("8.8.8.8:8787", 8787)      # public address
    assert not _private_host("192.168.0.72:9999", 8787)  # someone else's port
    assert not _private_host("burn-in.example.com:8787", 8787)

import asyncio
import hashlib
import json

import httpx
import pytest

from clipbot import __version__, updater
from clipbot.config import Settings, merge_incoming

REPO = "burn-in/app"
URL = f"https://github.com/{REPO}/releases/download/v99.0.0/BURN-IN-Setup.exe"
PAYLOAD = b"MZ" + b"installer" * 10


def settings():
    return merge_incoming(Settings(), {"updates": {"repo": REPO}})


def run_check(payload, status=200):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json=payload))) as http:
            return await updater.check(settings(), http)
    return asyncio.run(go())


def release(**changes):
    value = {"tag_name": "v99.0.0", "body": "Release notes", "assets": [
        {"name": "BURN-IN-Setup.exe", "size": len(PAYLOAD), "browser_download_url": URL,
         "digest": "sha256:" + hashlib.sha256(PAYLOAD).hexdigest()}]}
    value.update(changes)
    return value


def test_versions():
    assert updater.parse_version("v1.10.2") == (1, 10, 2)
    assert updater.newer("v1.10.0", "1.9.9")
    assert not updater.newer("1.0.0", "1.0")
    assert not updater.newer("v99.0.0-rc.1", "1.0.0")
    assert not updater.newer("garbage 99", "1.0.0")
    assert not updater.newer("2.0", "unknown")


def test_check_against_fake_github():
    out = run_check(release())
    assert out["available"] and out["latest"] == "99.0.0" and out["url"] == URL
    assert out["current"] == __version__
    assert out["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()


@pytest.mark.parametrize("payload", [[], None, {}, {"assets": None}, release(tag_name="junk"),
                                     release(prerelease=True), release(draft=True)])
def test_reject_malformed_or_unstable_releases(payload):
    result = run_check(payload)
    assert result["error"] and not result["available"]


@pytest.mark.parametrize("field,value", [
    ("browser_download_url", "https://evil.example/setup.exe"),
    ("browser_download_url", URL.replace(REPO, "somebody/else")),
    ("browser_download_url", URL.replace("https:", "http:")),
    ("size", -1), ("size", True), ("digest", "sha256:bad"),
])
def test_reject_invalid_installer_metadata(field, value):
    rel = release()
    rel["assets"][0][field] = value
    assert not run_check(rel)["available"]


def test_explicit_repo_overrides_packaged_default(tmp_path, monkeypatch):
    path = tmp_path / "release_config.json"
    path.write_text(json.dumps({"repo": "packaged/app"}))
    monkeypatch.setattr(updater, "resource_path", lambda *parts: path)
    assert updater.release_repo(Settings()) == "packaged/app"
    assert updater.release_repo(settings()) == REPO


def test_invalid_repo_and_asset_never_request_network(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "resource_path", lambda *parts: tmp_path / "missing")
    def unexpected(request):
        pytest.fail("invalid update settings made a network request")
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
            for incoming in ({}, {"repo": "../other"}, {"repo": REPO, "installer_asset": "../app.exe"}):
                result = await updater.check(merge_incoming(Settings(), {"updates": incoming}), http)
                assert result["error"] and not result["available"]
    asyncio.run(go())


def download(dest, payload=PAYLOAD, expected_size=len(PAYLOAD), digest=None, headers=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=payload, headers=headers))) as http:
            return await updater.download(URL, dest, settings(), http,
                                          expected_size=expected_size, expected_sha256=digest)
    return asyncio.run(go())


def test_download_verified_and_atomically_replaced(tmp_path):
    dl = tmp_path / "dl"; dl.mkdir()
    dest = dl / "BURN-IN-Setup.exe"
    dest.write_bytes(b"previous installer")
    assert download(dest, digest=hashlib.sha256(PAYLOAD).hexdigest()) == dest
    assert dest.read_bytes() == PAYLOAD
    assert sorted(p.name for p in dl.iterdir()) == [dest.name]


@pytest.mark.parametrize("kwargs", [
    {"expected_size": len(PAYLOAD) + 1},
    {"expected_size": len(PAYLOAD) - 1},
    {"digest": "0" * 64},
    {"payload": b"<html>error</html>", "expected_size": None},
    {"headers": {"Content-Length": str(len(PAYLOAD) + 1)}, "expected_size": None},
])
def test_failed_download_keeps_previous_file_and_cleans_partial(tmp_path, kwargs):
    dl = tmp_path / "dl"; dl.mkdir()
    dest = dl / "BURN-IN-Setup.exe"
    dest.write_bytes(b"previous installer")
    with pytest.raises(ValueError):
        download(dest, **kwargs)
    assert dest.read_bytes() == b"previous installer"
    assert sorted(p.name for p in dl.iterdir()) == [dest.name]


def test_download_network_error_cleans_partial(tmp_path):
    dl = tmp_path / "dl"; dl.mkdir()
    async def handler(request):
        raise httpx.ConnectError("offline", request=request)
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await updater.download(URL, dl / "BURN-IN-Setup.exe", settings(), http)
    with pytest.raises(httpx.ConnectError):
        asyncio.run(go())
    assert not list(dl.iterdir())


def test_installer_breaks_away_from_app_job(tmp_path, monkeypatch):
    exe = tmp_path / "BURN-IN-Setup.exe"
    exe.write_bytes(PAYLOAD)
    spawned = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *args, **kwargs: spawned.append((args, kwargs)))
    updater.run_installer(exe)
    assert spawned[0][1]["creationflags"] & updater.subprocess.CREATE_BREAKAWAY_FROM_JOB


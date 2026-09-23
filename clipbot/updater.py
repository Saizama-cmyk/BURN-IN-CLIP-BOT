"""Check GitHub releases and download verified Ashvane installers.

The stable installer asset name is shared by the desktop updater and web setup.
User settings and data remain in the existing ClipBot data directory.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from . import __version__
from .config import Settings, resource_path

logger = logging.getLogger("clipbot.updater")

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
SILENT_ARGS = ("/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/NORESTART")
DETACHED_PROCESS = 0x00000008
CHUNK = 1 << 20
_VERSION = re.compile(r"[vV]?(\d+(?:\.\d+)*)(?:\+[0-9A-Za-z.-]+)?")
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*")
_ASSET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.exe", re.IGNORECASE)
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


def parse_version(text: str) -> tuple[int, ...]:
    """Return a stable numeric version; reject junk and prerelease tags."""
    match = _VERSION.fullmatch(text.strip()) if isinstance(text, str) else None
    return tuple(int(n) for n in match[1].split(".")) if match else ()


def newer(latest: str, current: str) -> bool:
    new, old = parse_version(latest), parse_version(current)
    width = max(len(new), len(old))
    return bool(new and old) and new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def release_repo(settings: Settings) -> str:
    """Explicit user configuration wins over the repository baked into a release."""
    if settings.updates.repo.strip():
        return settings.updates.repo.strip()
    path = resource_path("assets", "release_config.json")
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("repo", "")).strip() if isinstance(data, dict) else ""
    except (OSError, ValueError) as exc:
        logger.warning("could not read bundled release source: %s", exc)
        return ""


def _source_error(repo: str, asset: str) -> str:
    if not repo:
        return "No update source set (Settings → Updates → GitHub repo)."
    if not _REPO.fullmatch(repo):
        return "The update source must be a GitHub repository in owner/name format."
    if not _ASSET.fullmatch(asset):
        return "The installer file name must be a plain .exe filename."
    return ""


def _valid_url(url: str, repo: str, asset: str) -> bool:
    try:
        parsed = urlsplit(url)
        prefix = f"/{repo}/releases/download/"
        path = unquote(parsed.path)
        rest = path[len(prefix):] if path.casefold().startswith(prefix.casefold()) else ""
        tag, separator, filename = rest.partition("/")
        return (parsed.scheme == "https" and parsed.netloc == "github.com"
                and not parsed.query and not parsed.fragment
                and bool(tag and separator) and filename == asset)
    except ValueError:
        return False


async def check(settings: Settings, http: httpx.AsyncClient) -> dict:
    u = settings.updates
    repo = release_repo(settings)
    out = {"current": __version__, "repo": repo, "available": False}
    error = _source_error(repo, u.installer_asset)
    if error:
        return {**out, "error": error}
    try:
        r = await http.get(GITHUB_API.format(repo=repo), timeout=u.timeout_s,
                           headers={"Accept": "application/vnd.github+json",
                                    "User-Agent": f"Ashvane/{__version__}"})
    except httpx.HTTPError as exc:
        logger.warning("update check failed: %s", exc)
        return {**out, "error": f"Couldn't reach GitHub: {exc}"}
    if r.status_code == 404:
        return {**out, "error": "No releases published yet."}
    if r.status_code != 200:
        return {**out, "error": f"GitHub HTTP {r.status_code}"}
    try:
        rel = r.json()
    except ValueError:
        return {**out, "error": "GitHub returned invalid release information."}
    if not isinstance(rel, dict) or not isinstance(rel.get("assets"), list):
        return {**out, "error": "GitHub returned invalid release information."}
    latest = str(rel.get("tag_name") or "")
    out.update(latest=latest.lstrip("vV"), notes=str(rel.get("body") or "")[:u.notes_max])
    if not parse_version(latest) or rel.get("draft") or rel.get("prerelease"):
        return {**out, "error": "The latest release is not a stable numbered version."}
    asset = next((a for a in rel["assets"] if isinstance(a, dict)
                  and a.get("name") == u.installer_asset), None)
    if not asset:
        return {**out, "error": f"Release {latest} has no {u.installer_asset}."}
    url, size = asset.get("browser_download_url"), asset.get("size")
    if not isinstance(url, str) or not _valid_url(url, repo, u.installer_asset):
        return {**out, "error": "The installer URL does not belong to the configured GitHub repository."}
    if type(size) is not int or size <= 0:
        return {**out, "error": "The release installer has an invalid size."}
    digest = asset.get("digest")
    sha256 = None
    if digest is not None:
        if not isinstance(digest, str) or not digest.startswith("sha256:") or not _SHA256.fullmatch(digest.split(":", 1)[1]):
            return {**out, "error": "The release installer has an invalid SHA-256 digest."}
        sha256 = digest.split(":", 1)[1].lower()
    return {**out, "url": url, "size": size, "sha256": sha256,
            "available": newer(latest, __version__), "error": ""}


async def download(url: str, dest: Path, settings: Settings, http: httpx.AsyncClient, *,
                   expected_size: int | None = None, expected_sha256: str | None = None) -> Path:
    """Atomically save an installer after size, signature and optional hash checks."""
    repo, asset = release_repo(settings), settings.updates.installer_asset
    error = _source_error(repo, asset)
    if error or not _valid_url(url, repo, asset):
        raise ValueError(error or "Untrusted installer URL.")
    if expected_size is not None and (type(expected_size) is not int or expected_size <= 0):
        raise ValueError("Invalid expected installer size.")
    if expected_sha256 is not None and not _SHA256.fullmatch(expected_sha256):
        raise ValueError("Invalid expected SHA-256 digest.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=dest.parent, prefix=f".{dest.name}.",
                                         suffix=".part", delete=False) as f:
            tmp = Path(f.name)
            digest = hashlib.sha256()
            received = 0
            async with http.stream("GET", url, follow_redirects=True,
                                   timeout=settings.updates.download_timeout_s) as r:
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"download HTTP {r.status_code}", request=r.request, response=r)
                content_length = r.headers.get("content-length")
                transfer_size = (int(content_length) if content_length and content_length.isdecimal()
                                 and not r.headers.get("content-encoding") else None)
                async for chunk in r.aiter_bytes(CHUNK):
                    received += len(chunk)
                    if expected_size is not None and received > expected_size:
                        raise ValueError("Installer download exceeds its published size.")
                    f.write(chunk)
                    digest.update(chunk)
            if (expected_size is not None and received != expected_size
                    or transfer_size is not None and received != transfer_size):
                raise ValueError("Installer download is incomplete; please try again.")
            if expected_sha256 is not None and digest.hexdigest() != expected_sha256.lower():
                raise ValueError("Installer SHA-256 verification failed; please try again.")
        with tmp.open("rb") as f:
            if f.read(2) != b"MZ":
                raise ValueError("The downloaded file is not a Windows installer.")
        tmp.replace(dest)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    logger.info("update downloaded: %s (%.0f MB)", dest.name, dest.stat().st_size / CHUNK)
    return dest


def run_installer(path: Path) -> None:
    """Start the installer detached; it closes this app, upgrades and restarts it."""
    path = path.resolve(strict=True)
    if path.suffix.lower() != ".exe":
        raise ValueError("The update installer must be an .exe file.")
    flags = DETACHED_PROCESS | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    subprocess.Popen([str(path), *SILENT_ARGS], creationflags=flags, close_fds=True)
    logger.info("update installer started: %s", path.name)


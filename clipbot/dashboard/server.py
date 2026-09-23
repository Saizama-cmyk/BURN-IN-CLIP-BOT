"""Dashboard API + static UI, served on 127.0.0.1 only.

Hardening for a localhost app: requests must carry a local Host header (blocks DNS rebinding)
and every state-changing call must send ``X-ClipBot: 1`` (a custom header forces a CORS
preflight, so other websites in the user's browser cannot trigger actions).
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import secrets
import time
import webbrowser
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .. import __version__
from ..aiplan import plan
from ..checklist import run_checklist
from ..clipability import AIUnavailable
from ..config import (LEAF_FIELDS, RESTART_FIELDS, SECRET_FIELDS, Settings, export_settings,
                      import_settings, masked_dump, merge_incoming, reset_section, resource_path)
from ..publishers.base import PublishError
from ..auth import COOKIE, AuthError, ProfileStore
from ..hardware import detect, recommend
from ..pets import GRAPH_HOURS, HOUR_S, PASSED, PetPlace, pet_state
from ..studio import ThemeStore, apply_style, current_style
from .. import syslog, updater
from ..media import (frames_dir, ollama_models_dir, poster_for, remux_segment, safe_name,
                     storage_report, stream_thumb)
from ..storage import KINDS, current_folder, queue_move
from ..editor import RenderError
from ..util import ERR_SNIPPET, CmdTimeout

logger = logging.getLogger("clipbot.server")

ACTION_HEADER = "x-clipbot"
BEARER = "bearer "
MESH_RANGE = ipaddress.ip_network("100.64.0.0/10")   # carrier-grade NAT space; Tailscale uses it
MESH_SUFFIX = ".ts.net"      # Tailscale MagicDNS, reachable only inside the tailnet
HTTPS_PORT = "443"
HOURS_PER_DAY = 24
_ID_RE = re.compile(r"^[0-9a-f]{6,32}$")
_OAUTH_PLATFORMS = ("youtube", "tiktok")
_PUBLIC = ("/static/", "/api/auth/", "/oauth/youtube/callback", "/oauth/tiktok/callback")
_PUBLIC_EXACT = {"/", "/remote", "/favicon.ico", "/api/control/focus"}   # shells only; their data is not


def _token(request: Request) -> str | None:
    """Session token: a browser sends the cookie, the phone app sends a bearer header."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith(BEARER):
        return auth[len(BEARER):].strip() or None
    return request.cookies.get(COOKIE)


def _is_app(request: Request, body: dict) -> bool:
    """A sign-in from the phone app rather than a browser. The app says so; older builds that
    don't are still recognised, because a browser always sends Origin / Sec-Fetch headers on a
    POST and the native app sends neither."""
    if body.get("device") == "phone":
        return True
    h = request.headers
    return not h.get("origin") and not h.get("sec-fetch-site") and not request.cookies.get(COOKIE)


def _private_host(host: str, port: int) -> bool:
    """True for "192.168.0.12:8787" and friends: a private address on our own port.

    Only consulted when the phone remote is switched on. Public addresses and odd ports are
    still refused, which keeps the DNS-rebinding guard intact."""
    name, _, got = host.partition(":")
    if got and got not in (str(port), HTTPS_PORT):
        return False
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        # a Tailscale MagicDNS name, which only resolves inside the user's own tailnet and is
        # served over HTTPS with a real certificate
        return name.lower().endswith(MESH_SUFFIX)
    # home network, or a private mesh like Tailscale/ZeroTier, which hands out 100.64.0.0/10
    return ip.is_private or ip in MESH_RANGE


def static_dir() -> Path:
    return resource_path("clipbot", "dashboard", "static")


def validation_errors(exc: ValidationError) -> list[dict]:
    return [{"field": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Ashvane</title>"
        "<body style='background:#0F1B2D;color:#E8EDF2;font-family:IBM Plex Sans,Segoe UI,sans-serif;"
        "display:grid;place-items:center;height:100vh;margin:0'><div style='max-width:560px'>"
        f"<h1 style='font-family:Barlow Condensed,sans-serif;color:#F2A93B'>{title}</h1>"
        f"<p>{body}</p><p style='color:#8FA3BF'>You can close this tab and return to Ashvane."
        "</p></div></body>")


def create_app(ctx) -> FastAPI:
    """``ctx`` is the running ``ClipBotApp`` (settings, pipeline, quit/restart hooks)."""
    app = FastAPI(title="Ashvane", docs_url=None, redoc_url=None,
                  openapi_url=None)
    profiles = ProfileStore()

    def a_cfg():
        return ctx.settings.app

    def _login_response(token: str, body: dict) -> JSONResponse:
        resp = JSONResponse(body)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/",
                        max_age=int(a_cfg().session_hours * 3600))
        return resp

    async def _json(request: Request) -> dict:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        return body if isinstance(body, dict) else {}

    @app.get("/api/auth/status")
    async def auth_status(request: Request):
        sess = await asyncio.to_thread(profiles.check, _token(request),
                                       a_cfg().auto_lock_min)
        return {"setup": profiles.needs_setup(), "profiles": profiles.profiles(),
                "active": profiles.last(),
                "signed_in": bool(sess and sess.profile_id == profiles.last()),
                "auto_lock_min": a_cfg().auto_lock_min,
                "min_password_len": a_cfg().min_password_len}

    @app.post("/api/auth/setup")
    async def auth_setup(request: Request):
        if not profiles.needs_setup():
            raise HTTPException(409, "already set up")
        b = await _json(request)
        try:
            prof = await asyncio.to_thread(profiles.create, str(b.get("name", "")),
                                           str(b.get("password", "")), a_cfg().min_password_len)
        except AuthError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        token = await asyncio.to_thread(profiles.create_session, prof["id"], a_cfg().session_hours)
        return _login_response(token, {"ok": True, "profile": prof})

    @app.post("/api/auth/login")
    async def auth_login(request: Request):
        b = await _json(request)
        pid = str(b.get("profile_id") or profiles.last())
        try:
            ok = await asyncio.to_thread(profiles.verify, pid, str(b.get("password", "")),
                                         a_cfg().login_max_attempts, a_cfg().login_lockout_s)
        except AuthError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=429)
        if not ok:
            return JSONResponse({"ok": False, "error": "Wrong password"}, status_code=401)
        phone = _is_app(request, b)
        hours = a_cfg().phone_session_days * HOURS_PER_DAY if phone else a_cfg().session_hours
        token = await asyncio.to_thread(profiles.create_session, pid, hours, phone)
        switch = pid != profiles.last()
        if switch:            # another profile has its own settings/data: restart into it
            await asyncio.to_thread(profiles.set_last, pid)
            asyncio.get_running_loop().call_later(0, ctx.request_quit, True)
        return _login_response(token, {"ok": True, "restarting": switch, "token": token})

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request):
        await asyncio.to_thread(profiles.revoke, request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.post("/api/profiles")
    async def add_profile(request: Request):
        b = await _json(request)
        try:
            prof = await asyncio.to_thread(profiles.create, str(b.get("name", "")),
                                           str(b.get("password", "")), a_cfg().min_password_len)
        except AuthError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return {"ok": True, "profile": prof}

    @app.post("/api/profiles/password")
    async def change_password(request: Request):
        b = await _json(request)
        try:
            await asyncio.to_thread(profiles.change_password, profiles.last(),
                                    str(b.get("old", "")), str(b.get("new", "")),
                                    a_cfg().min_password_len, a_cfg().login_max_attempts,
                                    a_cfg().login_lockout_s)
        except AuthError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return {"ok": True}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        d = ctx.settings.dashboard
        host = (request.headers.get("host") or "").lower()
        allowed = {f"{d.host}:{d.port}", f"localhost:{d.port}", d.host, "localhost"}
        if host not in allowed and not (d.remote and _private_host(host, d.port)):
            return JSONResponse({"error": "forbidden host"}, status_code=403)
        if request.method in ("POST", "PUT", "DELETE") and \
                request.headers.get(ACTION_HEADER) != "1":
            return JSONResponse({"error": f"missing {ACTION_HEADER} header"}, status_code=403)
        path = request.url.path
        if path == "/pet" or path.startswith("/api/pet/"):
            key = request.query_params.get("k") or request.headers.get("x-pet-key") or ""
            if not secrets.compare_digest(key, ctx.pet_token):
                return JSONResponse({"error": "bad pet key"}, status_code=401)
            return await call_next(request)
        if path not in _PUBLIC_EXACT and not path.startswith(_PUBLIC):
            if profiles.needs_setup():
                return JSONResponse({"error": "setup", "setup": True}, status_code=401)
            sess = await asyncio.to_thread(profiles.check, _token(request),
                                           ctx.settings.app.auto_lock_min)
            if sess is None or sess.profile_id != profiles.last():
                return JSONResponse({"error": "locked", "locked": True}, status_code=401)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=str(static_dir())), name="static")

    # ------------------------------------------------------------------ pages
    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(static_dir() / "index.html",
                            headers={"Cache-Control": "no-store"})

    @app.get("/remote", include_in_schema=False)
    async def remote():
        """The phone panel. Same session cookie as the desk, so it is behind the same password."""
        return FileResponse(static_dir() / "remote.html",
                            headers={"Cache-Control": "no-store"})

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        ico = resource_path("assets", "clipbot.ico")
        if not ico.exists():
            raise HTTPException(404)
        return FileResponse(ico)

    # ------------------------------------------------------------------ state
    # ------------------------------------------------------------------ desktop pet
    place = PetPlace(ctx.paths.data / "pet.json")

    @app.get("/pet", include_in_schema=False)
    async def pet_page():
        logger.info("pet window loaded")
        return FileResponse(static_dir() / "pet.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/pet/state")
    async def pet_data():
        now = time.time()
        history = await asyncio.to_thread(ctx.pipeline.store.candidate_histogram,
                                           now, GRAPH_HOURS, HOUR_S, PASSED)
        out = pet_state(await ctx.pipeline.snapshot(), ctx.settings, now, history=history)
        out["place"] = await asyncio.to_thread(place.load)
        return out

    @app.post("/api/pet/place")
    async def pet_place(request: Request):
        body = await _json(request)
        try:
            x, y = int(body["x"]), int(body["y"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "x and y required")
        await asyncio.to_thread(place.save, x, y)
        return {"ok": True}

    @app.post("/api/pet/species")
    async def pet_species(request: Request):
        species = str((await _json(request)).get("species", ""))
        try:
            new = merge_incoming(ctx.settings, {"pets": {"species": species}})
        except ValidationError as exc:
            return JSONResponse({"ok": False, "errors": validation_errors(exc)}, status_code=400)
        await ctx.commit_settings(new)
        return {"ok": True, "species": new.pets.species}

    @app.post("/api/pet/log")
    async def pet_log(request: Request):
        body = await _json(request)
        msg = str(body.get("msg", ""))[:ERR_SNIPPET]
        # only real page errors are issues; the boot line is just a note
        (logger.warning if body.get("bad") else logger.info)("pet page: %s", msg)
        return {"ok": True}

    # ------------------------------------------------------------------ live viewer ("see what it sees")
    _SEG_NAME = re.compile(r"^seg_\d{14}$")

    @app.get("/api/watch/{key}")
    async def watch_state(key: str):
        """Everything the live viewer shows: newest buffer segments, detector state, chat."""
        cap, target = ctx.pipeline.captures.get(key), ctx.pipeline.targets.get(key)
        if target is None:
            raise HTTPException(404, "not monitoring that stream")
        segs = (await asyncio.to_thread(cap.segments)) if cap else []
        v, d = ctx.settings.viewer, ctx.settings.detector
        samples = await asyncio.to_thread(ctx.pipeline.store.recent_candidates, ctx.settings.dashboard.samples_limit)
        clips = [c for c in samples if (c.get("event") or {}).get("target", {}).get("login") == target.login]
        return {"key": key, "platform": str(target.platform), "login": target.login,
                "name": target.display_name, "category": target.category, "viewers": target.viewers,
                "title": target.title, "url": target.url,
                "segments": [{"name": sg.path.stem, "start": sg.start, "end": sg.end}
                             for sg in segs[-v.segments_ahead:]],
                "stats": ctx.pipeline.detector.stats(key),
                "thresholds": {"z": d.z_threshold, "keyword": d.keyword_threshold, "audio_z": d.audio_z_threshold},
                "chat": ctx.pipeline.detector.recent_chat(key, v.chat_lines),
                "clips": [{"id": c["id"], "stage": c.get("stage"), "created_at": c.get("created_at"),
                           "title": (c.get("verdict") or {}).get("title", "")} for c in clips[:v.clips_shown]]}

    @app.get("/media/live/{key}/{name}.mp4")
    async def watch_segment(key: str, name: str):
        """One buffer segment remuxed (no re-encode) to fragmented MP4 for the browser; cached."""
        cap = ctx.pipeline.captures.get(key)
        if cap is None or not _SEG_NAME.match(name):
            raise HTTPException(404)
        src = cap.folder / f"{name}.ts"
        if not src.exists():
            raise HTTPException(404, "segment gone")
        out = await remux_segment(src, ctx.pipeline.paths.work / "live" / safe_name(key), ctx.settings)
        if out is None:
            raise HTTPException(500, "could not remux segment")
        return FileResponse(out, media_type="video/mp4", headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/pet/thumb.jpg")
    async def pet_thumb(key: str):
        return await monitor_thumb(key)

    # ------------------------------------------------------------------ studio
    themes = ThemeStore(ctx.paths.data / "themes.json")

    @app.get("/api/studio")
    async def studio_info():
        return {"current": current_style(ctx.settings), "themes": await asyncio.to_thread(themes.all),
                "keep_source": ctx.settings.edit.keep_raw, "fonts": ctx.settings.studio.font_choices,
                "watermark": ctx.settings.brand.watermark}

    @app.get("/api/studio/clip/{cid}")
    async def studio_clip(cid: str):
        if not _ID_RE.match(cid):
            raise HTTPException(400, "bad id")
        info = await ctx.pipeline.studio_clip(cid)
        if info is None:
            raise HTTPException(404, "clip not found")
        return info

    @app.post("/api/studio/render")
    async def studio_render(request: Request):
        b = await _json(request)
        cid = str(b.get("cid", ""))
        if not _ID_RE.match(cid):
            raise HTTPException(400, "bad id")
        try:
            return await ctx.pipeline.studio_render(
                cid, b.get("style") or {}, float(b.get("trim_start", 0)), float(b.get("trim_end", 0)),
                str(b.get("hook", "")), bool(b.get("apply")))
        except ValidationError as exc:
            return JSONResponse({"ok": False, "errors": validation_errors(exc)}, status_code=400)
        except (RenderError, CmdTimeout, OSError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": f"render failed: {exc}"}, status_code=500)

    @app.get("/media/{cid}/source.mp4")
    async def studio_source(cid: str):
        if not _ID_RE.match(cid):
            raise HTTPException(400, "bad id")
        c = await asyncio.to_thread(ctx.pipeline.store.get_candidate, cid)
        if c is None or not (c.raw_path and Path(c.raw_path).exists()):
            raise HTTPException(404, "source cut not kept")
        return FileResponse(c.raw_path, media_type="video/mp4")

    @app.get("/media/studio/{cid}.mp4")
    async def studio_media(cid: str):
        if not _ID_RE.match(cid):
            raise HTTPException(400, "bad id")
        path = ctx.pipeline.studio_preview_path(cid)
        if not path.exists():
            raise HTTPException(404, "no preview rendered yet")
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})

    @app.post("/api/studio/themes")
    async def studio_save_theme(request: Request):
        b = await _json(request)
        try:
            apply_style(ctx.settings, b.get("style") or {})      # validate before storing
            await asyncio.to_thread(themes.save, str(b.get("name", "")), b.get("style") or {})
        except ValidationError as exc:
            return JSONResponse({"ok": False, "errors": validation_errors(exc)}, status_code=400)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return {"ok": True, "themes": await asyncio.to_thread(themes.all)}

    @app.delete("/api/studio/themes/{name}")
    async def studio_delete_theme(name: str):
        ok = await asyncio.to_thread(themes.delete, name)
        return {"ok": ok, "themes": await asyncio.to_thread(themes.all)}

    @app.post("/api/studio/default")
    async def studio_default(request: Request):
        """Make a Studio look the default for every new clip (writes Settings → Editor)."""
        try:
            new = apply_style(ctx.settings, (await _json(request)).get("style") or {})
        except ValidationError as exc:
            return JSONResponse({"ok": False, "errors": validation_errors(exc)}, status_code=400)
        restart = await ctx.commit_settings(new)
        return {"ok": True, "restart_required": restart}

    @app.post("/api/studio/keep_source")
    async def studio_keep_source():
        await ctx.commit_settings(merge_incoming(ctx.settings, {"edit": {"keep_raw": True}}))
        return {"ok": True}

    @app.get("/api/update/check")
    async def update_check():
        return await updater.check(ctx.settings, ctx.pipeline.http)

    @app.post("/api/update/install")
    async def update_install():
        info = await updater.check(ctx.settings, ctx.pipeline.http)
        if not info.get("available"):
            return JSONResponse({"ok": False, "error": info.get("error") or "Already up to date."},
                                status_code=400)
        try:
            path = await updater.download(info["url"], ctx.pipeline.paths.work / "updates" /
                                          ctx.settings.updates.installer_asset, ctx.settings,
                                          ctx.pipeline.http, expected_size=info.get("size"),
                                          expected_sha256=info.get("sha256"))
            await asyncio.to_thread(updater.run_installer, path)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": f"Update failed: {exc}"}, status_code=502)
        return {"ok": True, "version": info["latest"]}

    @app.get("/api/system/panel")
    async def system_panel():
        """Status card panel: what the status means, events, issues and analytics."""
        snap = await ctx.pipeline.snapshot()
        lines = await asyncio.to_thread(syslog.tail_lines, ctx.pipeline.paths.logs / "clipbot.log",
                                        ctx.settings.dashboard.panel_log_kb * 1024)
        samples = await asyncio.to_thread(ctx.pipeline.store.recent_candidates,
                                          ctx.settings.dashboard.panel_samples)
        log = syslog.split(syslog.parse(lines), ctx.settings.dashboard.panel_entries)
        return {"status": syslog.explain(snap, ctx.settings), "backlog": snap.get("backlog"),
                "gpu": snap.get("gpu"), "ai": snap.get("ai"), "whisper": snap.get("whisper"),
                "chat": snap.get("chat"), "monitors": len(snap.get("monitors") or []), **log,
                "analytics": syslog.analytics(samples, snap)}

    from ..agent.models import MODELS, ModelMirror
    mirror = ModelMirror(ctx.paths.data, ctx.settings.assistant.watch_timeout_s)

    @app.get("/api/assistant/models/{model_id}")
    async def phone_model_status(model_id: str):
        """How far the PC's copy of a phone model has got."""
        return mirror.status(model_id)

    @app.post("/api/assistant/models/{model_id}/fetch")
    async def phone_model_fetch(model_id: str):
        """Have the PC download a phone model, so the phone can copy it over home Wi-Fi."""
        return mirror.fetch(model_id)

    @app.get("/api/assistant/models/{model_id}/file")
    async def phone_model_file(model_id: str):
        """The model file itself, resumable (Range requests), for the phone to copy."""
        if model_id not in MODELS or not mirror.status(model_id).get("ready"):
            raise HTTPException(404, "not on the PC yet")
        return FileResponse(mirror.path(model_id), media_type="application/octet-stream")

    @app.post("/api/remote/test")
    async def remote_test():
        """Try every address the phone could use, from this PC, and say which one to type."""
        from ..remote_check import check
        return await check(ctx.settings, ctx.settings.dashboard.remote_test_timeout_s)

    @app.get("/api/state")
    async def state():
        snap = await ctx.pipeline.snapshot()
        snap["restart_pending"] = sorted(ctx.restart_pending)
        snap["refresh_ms"] = ctx.settings.dashboard.refresh_ms
        snap["sound"] = {"on": ctx.settings.dashboard.ui_sounds, "volume": ctx.settings.dashboard.ui_volume}
        snap["viewer_poll_ms"] = ctx.settings.viewer.poll_ms
        snap["viewer_max_lag_s"] = ctx.settings.viewer.live_max_lag_s
        snap["viewer_target_lag_s"] = ctx.settings.viewer.live_target_lag_s
        snap["viewer_keep_s"] = ctx.settings.viewer.live_keep_s
        snap["updates_on_start"] = ctx.settings.updates.check_on_start and bool(ctx.settings.updates.repo)
        snap["remote_url"] = ctx.lan_url if ctx.settings.dashboard.remote else ""
        snap["version"] = __version__
        return snap

    @app.post("/api/chat")
    async def chat(request: Request):
        """One turn of conversation with the local model (the phone's Assistant tab)."""
        body = await _json(request)
        raw = body.get("messages")
        if not isinstance(raw, list) or not raw:
            raise HTTPException(422, "messages must be a non-empty list")
        ai = ctx.settings.ai
        history = [{"role": "assistant" if m.get("role") == "assistant" else "user",
                    "content": str(m.get("content", ""))[:ai.transcript_max_chars]}
                   for m in raw[-ai.chat_history:] if str(m.get("content", "")).strip()]
        if not history:
            raise HTTPException(422, "nothing to say")
        model = ai.chat_model.strip() or plan(ctx.settings, ctx.pipeline.vram_gb).judge_model
        # the phone sends its own instructions, rules and skill; the PC's default otherwise
        system = str(body.get("system") or "").strip()[:ai.transcript_max_chars] or ai.chat_system
        payload = {"model": model, "stream": False,
                   "messages": [{"role": "system", "content": system}, *history],
                   "options": {"num_ctx": ai.vision_num_ctx, "temperature": ai.chat_temperature}}
        try:
            r = await ctx.pipeline.http.post(f"{ai.ollama_url.rstrip('/')}/api/chat", json=payload,
                                             timeout=ai.chat_timeout_s)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            return JSONResponse({"error": f"The local model did not answer: {exc}"},
                                status_code=503)
        reply = (r.json().get("message") or {}).get("content", "").strip()
        return {"reply": reply, "model": model}

    # ------------------------------------------------------------------ phone assistant's eyes
    # The phone's own model thinks; these only turn a picture or a video into text for it.
    @app.post("/api/assistant/upload")
    async def assistant_upload(request: Request, name: str = ""):
        """A file from the phone, streamed straight to disk. Returns its id."""
        from ..agent.watch import MB, new_upload, prune_uploads
        a = ctx.settings.assistant
        await asyncio.to_thread(prune_uploads, ctx.paths.data, a.uploads_keep_h)
        uid, dest = new_upload(ctx.paths.data, name)
        limit, size = a.upload_max_mb * MB, 0
        try:
            with dest.open("wb") as fh:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"bigger than {a.upload_max_mb} MB")
                    fh.write(chunk)
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        return {"id": uid, "bytes": size}

    @app.post("/api/assistant/look")
    async def assistant_look(request: Request):
        """Pictures in, a description out."""
        b = await _json(request)
        images = [str(i) for i in (b.get("images") or []) if isinstance(i, str) and i]
        a = ctx.settings.assistant
        if not images:
            raise HTTPException(422, "no pictures")
        if len(images) > a.images_max:
            raise HTTPException(422, f"at most {a.images_max} pictures at once")
        question = str(b.get("question") or "").strip()
        prompt = a.look_prompt + (f"\n\nQuestion: {question}" if question else "")
        try:
            text = await ctx.pipeline.vision.look(prompt, images)
        except (httpx.HTTPError, RuntimeError) as exc:
            return JSONResponse({"error": f"The PC's vision model did not answer: {exc}"},
                                status_code=503)
        return {"description": text}

    @app.post("/api/assistant/watch")
    async def assistant_watch(request: Request):
        """A video link or upload:<id> in; what is said and shown out."""
        from ..agent.watch import watch
        b = await _json(request)
        result = await watch(ctx, str(b.get("target") or ""))
        if "error" in result:
            return JSONResponse(result, status_code=422)
        return result

    @app.get("/api/setup")
    async def setup():
        return await run_checklist(ctx.settings, ctx.pipeline.http)

    # ------------------------------------------------------------------ settings
    @app.get("/api/settings")
    async def get_settings():
        return {"settings": masked_dump(ctx.settings),
                "restart_pending": sorted(ctx.restart_pending)}

    @app.get("/api/settings/schema")
    async def get_schema():
        return {"schema": Settings.model_json_schema(),
                "leaf_count": len(LEAF_FIELDS),
                "secret": [".".join(p) for p in sorted(SECRET_FIELDS)],
                "restart": [".".join(p) for p in sorted(RESTART_FIELDS)]}

    async def _save(new: Settings) -> dict:
        restart = await ctx.commit_settings(new)
        return {"ok": True, "restart_required": restart,
                "restart_pending": sorted(ctx.restart_pending),
                "settings": masked_dump(ctx.settings)}

    @app.put("/api/settings")
    async def put_settings(request: Request):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"ok": False, "errors": [{"field": "", "msg": "invalid JSON"}]},
                                status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "errors": [{"field": "", "msg": "expected object"}]},
                                status_code=400)
        try:
            new = merge_incoming(ctx.settings, body.get("settings", body))
        except ValidationError as exc:
            return JSONResponse({"ok": False, "errors": validation_errors(exc)}, status_code=422)
        return await _save(new)

    @app.post("/api/settings/reset/{section}")
    async def reset(section: str):
        try:
            new = reset_section(ctx.settings, section)
        except KeyError:
            raise HTTPException(404, f"unknown section {section}")
        return await _save(new)

    @app.get("/api/settings/export")
    async def export(include_secrets: bool = False):
        data = export_settings(ctx.settings, include_secrets)
        return Response(json.dumps(data, indent=2), media_type="application/json",
                        headers={"Content-Disposition":
                                 'attachment; filename="clipbot-settings.json"'})

    @app.post("/api/settings/import")
    async def import_(request: Request):
        try:
            data = await request.json()
            new = import_settings(ctx.settings, data)
        except json.JSONDecodeError:
            return JSONResponse({"ok": False, "errors": [{"field": "", "msg": "not a JSON file"}]},
                                status_code=400)
        except ValidationError as exc:
            return JSONResponse({"ok": False, "errors": validation_errors(exc)}, status_code=422)
        return await _save(new)

    @app.get("/api/ai/models")
    async def ai_models():
        try:
            return {"ok": True, "models": await ctx.pipeline.clipability.list_models()}
        except AIUnavailable as exc:
            return {"ok": False, "models": [], "error": str(exc)}

    # ------------------------------------------------------------------ control
    @app.post("/api/control/pause")
    async def pause():
        await ctx.pipeline.pause()
        if ctx.on_state_change:
            ctx.on_state_change()
        return {"ok": True, "paused": True}

    @app.post("/api/control/resume")
    async def resume():
        await ctx.pipeline.resume()
        if ctx.on_state_change:
            ctx.on_state_change()
        return {"ok": True, "paused": False}

    @app.post("/api/control/quit")
    async def quit_():
        asyncio.get_running_loop().call_later(0, ctx.request_quit)
        return {"ok": True}

    @app.post("/api/control/restart")
    async def restart():
        asyncio.get_running_loop().call_later(0, ctx.request_quit, True)
        return {"ok": True}

    @app.post("/api/control/focus")
    async def focus():
        if ctx.on_focus:
            ctx.on_focus()
            return {"ok": True, "window": True}
        return {"ok": True, "window": False}

    @app.post("/api/control/open_clips")
    async def open_clips():
        folder = ctx.pipeline.paths.clips
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(folder))  # noqa: S606 — opens Explorer on our own folder
        return {"ok": True, "path": str(folder)}

    # ------------------------------------------------------------------ media
    @app.get("/media/{cid}")
    async def media(cid: str):
        if not _ID_RE.match(cid):
            raise HTTPException(400, "bad id")
        path = await asyncio.to_thread(ctx.pipeline.candidate_media, cid)
        if path is None:
            raise HTTPException(404, "no video for this sample")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/media/{cid}/poster.jpg")
    async def poster(cid: str):
        if not _ID_RE.match(cid):
            raise HTTPException(400, "bad id")
        c = await asyncio.to_thread(ctx.pipeline.store.get_candidate, cid)
        src = await asyncio.to_thread(ctx.pipeline.candidate_media, cid)
        if c is None or src is None:
            raise HTTPException(404, "no video")
        spike = max(0.0, c.event.t_wall - c.start_wall)
        at = spike if src.name.endswith("_raw.mp4") else max(
            0.0, spike - float(c.verdict.get("trim_start", 0.0) or 0.0))
        out = await poster_for(src, cid, at, ctx.settings, ctx.pipeline.paths)
        if out is None:
            raise HTTPException(404, "no poster")
        return FileResponse(out, media_type="image/jpeg")

    @app.get("/media/{cid}/frames/{n}.jpg")
    async def vision_frame(cid: str, n: int):
        if not _ID_RE.match(cid):
            raise HTTPException(400, "bad id")
        f = frames_dir(ctx.pipeline.paths, cid) / f"{n}.jpg"
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f, media_type="image/jpeg")

    @app.get("/api/monitor/thumb.jpg")
    async def monitor_thumb(key: str):
        cap = ctx.pipeline.captures.get(key)
        segs = await asyncio.to_thread(cap.segments) if cap else []
        if not segs:
            raise HTTPException(404, "no buffer yet")
        out = await stream_thumb(segs[-1].path, key, ctx.settings, ctx.pipeline.paths)
        if out is None:
            raise HTTPException(404)
        return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/clips")
    async def clips():
        d = ctx.settings.dashboard
        return {"clips": await asyncio.to_thread(ctx.pipeline.store.recent_candidates, d.samples_limit),
                "posts": await asyncio.to_thread(ctx.pipeline.store.recent_posts, d.posts_limit)}

    @app.post("/api/clips/{cid}/publish")
    async def publish_now(cid: str, request: Request):
        if not _ID_RE.match(cid):
            raise HTTPException(400, "bad id")
        platforms = [str(p) for p in (await _json(request)).get("platforms", [])]
        if not platforms:
            raise HTTPException(400, "pick at least one platform")
        try:
            return await ctx.pipeline.publish_now(cid, platforms)
        except (RenderError, CmdTimeout, OSError) as exc:
            return JSONResponse({"ok": False, "error": f"render failed: {exc}"}, status_code=500)

    @app.get("/api/storage")
    async def storage():
        rep_ = await asyncio.to_thread(storage_report, ctx.pipeline.paths, ollama_models_dir())
        rep_["kinds"] = list(KINDS)
        return rep_

    @app.post("/api/storage/pick")
    async def pick_folder():
        if not ctx.pick_folder:
            return JSONResponse({"ok": False, "error": "the folder picker needs the desktop app"},
                                status_code=501)
        path = await asyncio.to_thread(ctx.pick_folder)
        return {"ok": bool(path), "path": path or ""}

    @app.post("/api/storage/move")
    async def storage_move(request: Request):
        b = await _json(request)
        kind, dest = str(b.get("kind", "")), str(b.get("dest", "")).strip()
        if kind not in KINDS or not dest:
            raise HTTPException(400, "kind and dest required")
        src = current_folder(ctx.pipeline.paths, kind)
        if b.get("move", True) and src is not None and src.exists():
            await asyncio.to_thread(queue_move, kind, src, Path(dest))
        return await _save(merge_incoming(ctx.settings, {"app": {KINDS[kind]: dest}}))

    @app.post("/api/system/scan")
    async def system_scan(request: Request):
        b = await _json(request)
        hw = await detect(ctx.settings, ctx.pipeline.paths, ctx.pipeline.http,
                          ctx.pipeline.transcriber.status, bool(b.get("network", True)))
        return {"hardware": hw.to_dict(), "recommendations": recommend(hw, ctx.settings)}

    @app.post("/api/system/apply")
    async def system_apply(request: Request):
        b = await _json(request)
        patch: dict = {}
        for item in b.get("apply", []):
            section, name = str(item["field"]).split(".", 1)
            patch.setdefault(section, {})[name] = item["value"]
        try:
            new = merge_incoming(ctx.settings, patch)
        except ValidationError as exc:
            return JSONResponse({"ok": False, "errors": validation_errors(exc)}, status_code=422)
        return await _save(new)

    @app.get("/api/analytics")
    async def analytics():
        snap = ctx.pipeline.analytics.snapshot()
        snap["history"] = await asyncio.to_thread(ctx.pipeline.store.account_history, 0)
        return snap

    @app.post("/api/analytics/refresh")
    async def analytics_refresh():
        await ctx.pipeline.analytics.tick(force=True)
        return {"ok": True}

    @app.post("/api/clip_import/run")
    async def clip_import_run():
        return {"ok": True, "imported": await ctx.pipeline.importer.run_once()}

    # ------------------------------------------------------------------ oauth
    @app.get("/oauth/{platform}/start")
    async def oauth_start(platform: str):
        if platform not in _OAUTH_PLATFORMS:
            raise HTTPException(404)
        try:
            return RedirectResponse(ctx.pipeline.oauth.start_url(platform))
        except PublishError as exc:
            return _page("Can't connect yet", str(exc))

    @app.post("/api/oauth/{platform}/open")
    async def oauth_open(platform: str):
        """Open the provider's sign-in page in the system browser. The signed-in dashboard
        asks for it, so the browser never needs an Ashvane session; the callback is public
        and guarded by the one-time state."""
        if platform not in _OAUTH_PLATFORMS:
            raise HTTPException(404)
        try:
            url = ctx.pipeline.oauth.start_url(platform)
        except PublishError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        opened = await asyncio.to_thread(webbrowser.open, url)
        logger.info("%s sign-in opened in the browser (%s)", platform, "ok" if opened else "failed")
        return {"ok": bool(opened), "url": url}

    @app.get("/oauth/{platform}/callback")
    async def oauth_callback(platform: str, code: str = "", state: str = "", error: str = "",
                             error_description: str = ""):
        if platform not in _OAUTH_PLATFORMS:
            raise HTTPException(404)
        if error or not code:
            return _page("Connection cancelled", error_description or error or "no code returned")
        try:
            await ctx.pipeline.oauth.callback(platform, code, state)
        except PublishError as exc:
            return _page("Connection failed", str(exc))
        return _page(f"{platform.title()} connected", "Ashvane can now post for you.")

    @app.post("/oauth/{platform}/paste")
    async def oauth_paste(platform: str, request: Request):
        if platform not in _OAUTH_PLATFORMS:
            raise HTTPException(404)
        try:
            landed = str((await request.json()).get("url", ""))
            await ctx.pipeline.oauth.callback_from_url(platform, landed)
        except (json.JSONDecodeError, AttributeError):
            return JSONResponse({"ok": False, "error": "send {\"url\": \"…\"}"}, status_code=400)
        except PublishError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return {"ok": True}

    @app.post("/oauth/{platform}/disconnect")
    async def oauth_disconnect(platform: str):
        if platform not in _OAUTH_PLATFORMS:
            raise HTTPException(404)
        await asyncio.to_thread(ctx.pipeline.oauth.disconnect, platform)
        return {"ok": True}

    return app

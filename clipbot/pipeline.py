"""Orchestrator: discovery → captures + chat → detector → assemble → QC → transcribe →
clipability → edit → scheduler.

Every spike becomes one candidate task that walks the stages, each stage gated by its own
limit (``workers.pipelines`` for cuts, ``workers.transcribers`` for Whisper, one clipability
call at a time, ``workers.editors`` renders). Rejected / failed candidates keep their row and
lose their raw file.

Backlog failsafe: pressure = in-flight candidates / ``backlog.capacity``. At ≥ ``pause_at`` all
captures pause and new spikes are ignored; at ≤ ``resume_at`` captures resume (hysteresis).
"""
from __future__ import annotations

import asyncio
import json
import datetime
import logging
import shutil
import time
from collections import Counter
from pathlib import Path

import httpx

from . import __version__
from .assembler import AssembleError, assemble, quality_check
from .capture import CaptureManager
from .chat import ChatHub
from .clipability import looks_like_dead_air, SCORE_MAX, AIUnavailable, Clipability
from .config import AppPaths, Settings, atomic_write, ffmpeg_exe, ffprobe_exe, merge_incoming
from .db import Store
from .detector import Detector
from .discovery import Discovery
from .editor import RenderError, probe_media, render
from .models import Candidate, SpikeEvent, Stage, StreamTarget
from .publishers import OAuthManager, TokenStore, build_publishers
from .scheduler import Scheduler
from .transcribe import Transcriber
from .util import CmdTimeout, child_count, run_cmd, terminate_all
from .vision import Vision, find_facecam
from .copywriter import Copywriter
from .analytics import Analytics
from .clip_import import ClipImporter
from .media import safe_name, delete_candidate_media, frames_dir, free_bytes
from .studio import apply_style, clean_style
from .aiplan import vram_gb
from .ollama_setup import ModelSetup
from .sweeper import sweep
from .storage import clips_target
from .safety import Filter

logger = logging.getLogger("clipbot.pipeline")
RAW_SUFFIX = ".mp4"       # the source cut; everything else in work/ is scratch
UNFINISHED = ("queued", "qc", "transcribe", "clipability", "edit")
SWEEP_IDLE_S = 300.0      # nothing to do: look again in five minutes

GIB = 1024 * 1024 * 1024

# nvidia-smi query field → key in the dashboard's GPU readout
_GPU_FIELDS = (("name", "name"), ("utilization.gpu", "util"), ("memory.used", "mem_used"),
               ("memory.total", "mem_total"), ("temperature.gpu", "temp"))


class Limiter:
    """A semaphore whose limit can change at runtime."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.active = 0
        self._cond = asyncio.Condition()

    async def __aenter__(self) -> None:
        async with self._cond:
            await self._cond.wait_for(lambda: self.active < self.limit)
            self.active += 1

    async def __aexit__(self, *exc) -> None:
        async with self._cond:
            self.active -= 1
            self._cond.notify_all()

    async def set_limit(self, limit: int) -> None:
        async with self._cond:
            self.limit = limit
            self._cond.notify_all()


class Pipeline:
    def __init__(self, settings: Settings, paths: AppPaths) -> None:
        self.settings = settings
        self.paths = paths
        paths.ensure()
        self.store = Store(paths.db)
        self.tokens = TokenStore(paths.tokens)
        self.http: httpx.AsyncClient | None = None
        self.detector = Detector(settings.detector)
        self.transcriber = Transcriber(settings, paths.models)
        self.targets: dict[str, StreamTarget] = {}
        self.manual_paused = False
        self.failsafe_paused = False
        self._failsafe_since = 0.0
        self._relief_at = 0.0
        self.slot_relief = 0          # streams temporarily given up while catching up
        self.disk_paused = False
        self.captures_paused = False
        self.spikes_ignored = 0
        self.spikes_total = 0
        self.started_at = time.time()
        self.gpu: dict | None = None
        self._tasks: list[asyncio.Task] = []
        self._inflight: dict[str, asyncio.Task] = {}
        self._stage_of: dict[str, Stage] = {}
        self._audio_seen: dict[str, str] = {}
        self._discovery_wake = asyncio.Event()
        self._pause_lock = asyncio.Lock()
        self._running = False
        self.blocked = ""
        # created in start() (need the HTTP client / running loop)
        self.discovery: Discovery
        self.captures: CaptureManager
        self.chat: ChatHub
        self.clipability: Clipability
        self.oauth: OAuthManager
        self.scheduler: Scheduler
        self._assemble_limit = Limiter(settings.workers.pipelines)
        self._edit_limit = Limiter(settings.workers.editors)
        self._ai_limit = Limiter(settings.workers.ai_stage)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        s = self.settings
        self.http = httpx.AsyncClient(follow_redirects=True,
                                      timeout=s.discovery.http_timeout_s)
        self.discovery = Discovery(s, self.http)
        self.captures = CaptureManager(s, self.paths.buffer, self.discovery.kick_channel)
        self.chat = ChatHub(s, self._on_chat, self.discovery.kick_channel)
        gpu_lock = asyncio.Lock()          # vision model and judge never run at the same time
        self.clipability = Clipability(s, self.http, gpu_lock)
        self.vision = Vision(s, self.http, gpu_lock)
        self.copywriter = Copywriter(s, self.http, gpu_lock)
        self.oauth = OAuthManager(s, self.http, self.tokens)
        self.publishers = build_publishers(s, self.http, self.tokens, self.oauth)
        self.scheduler = Scheduler(s, self.store, self.publishers, self.paths)
        self.analytics = Analytics(s, self.store, self.publishers, self.paths)
        self.scheduler.analytics = self.analytics
        self.importer = ClipImporter(s, self.store, self.discovery, self.paths, self._launch,
                                     lambda: list(self.targets.values()))
        self._running = True
        await self._recover()
        self.scheduler.start()
        self.analytics.start()
        self.model_setup = ModelSetup(s, self.http)
        self._tasks.append(asyncio.create_task(self._first_run_models(), name="model-setup"))
        loops = [(self._gpu_loop(), "gpu"), (self._housekeeping_loop(), "housekeeping")]
        missing = [n for n, exe in (("ffmpeg", ffmpeg_exe(s)), ("ffprobe", ffprobe_exe(s))) if not exe]
        if s.app.check_ffmpeg_on_start and missing:
            self.blocked = (f"{' and '.join(missing)} not found — captures are off until you install "
                            f"it or set Settings → Paths, then restart BURN-IN")
            logger.error(self.blocked)
        else:
            loops += [(self._discovery_loop(), "discovery"), (self._detector_loop(), "detector"),
                      (self._audio_loop(), "audio")]
            self.importer.start()
        for coro, name in loops:
            self._tasks.append(asyncio.create_task(coro, name=name))
        logger.info("pipeline started (data: %s)", self.paths.data)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        logger.info("pipeline stopping")
        grace = self.settings.app.shutdown_timeout_s
        for t in self._tasks + list(self._inflight.values()):
            t.cancel()
        await asyncio.gather(*self._tasks, *self._inflight.values(), return_exceptions=True)
        self._tasks.clear()
        await asyncio.gather(self.captures.stop_all(), self.chat.stop(), self.scheduler.stop(),
                             self.analytics.stop(), self.importer.stop(), return_exceptions=True)
        await terminate_all(grace)
        if self.http:
            await self.http.aclose()
        await asyncio.to_thread(self.transcriber.unload)
        self.store.close()
        logger.info("pipeline stopped")

    def apply_settings(self, s: Settings) -> None:
        """Hot-swap settings into every component (restart-only fields wait for a restart)."""
        old = self.settings
        self.settings = s
        self.detector.apply(s.detector)
        self.transcriber.apply(s)
        self.discovery.apply(s)
        self.captures.apply(s)
        self.chat.apply(s)
        self.clipability.apply(s)
        self.vision.apply(s)
        self.copywriter.apply(s)
        self.analytics.apply(s)
        self.importer.apply(s)
        self.oauth.apply(s)
        self.scheduler.apply(s)
        loop = asyncio.get_running_loop()
        loop.create_task(self._assemble_limit.set_limit(s.workers.pipelines))
        loop.create_task(self._edit_limit.set_limit(s.workers.editors))
        loop.create_task(self._ai_limit.set_limit(s.workers.ai_stage))
        if (old.twitch, old.kick, old.discovery) != (s.twitch, s.kick, s.discovery):
            self._discovery_wake.set()
        loop.create_task(self._abandon_stale())
        loop.create_task(self._refresh_failsafe())
        self._tasks.append(loop.create_task(self._sweep_loop(), name="sweeper"))

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ failsafe
    @property
    def in_flight(self) -> int:
        return len(self._inflight)

    @property
    def pressure(self) -> float:
        return self.in_flight / self.settings.backlog.capacity

    @property
    def paused(self) -> bool:
        return self.manual_paused or self.failsafe_paused or self.disk_paused

    def _update_failsafe(self, pressure: float) -> bool:
        """Hysteresis: engage at ≥ pause_at, release at ≤ resume_at. Returns the state.

        A PC that is simply watching more streams than it can judge would otherwise sit in the
        failsafe for hours, which is noise, not information. So when the failsafe has been on for
        ``backlog.relief_after_s`` we drop a few streams and keep dropping until it drains, then
        hand the slots back one step at a time."""
        b = self.settings.backlog
        now = time.monotonic()
        if not self.failsafe_paused and pressure >= b.pause_at:
            self.failsafe_paused = True
            self._failsafe_since = now
            logger.warning("backlog failsafe ON: pressure %.2f ≥ %.2f — captures paused",
                           pressure, b.pause_at)
        elif self.failsafe_paused and pressure <= b.resume_at:
            self.failsafe_paused = False
            self._failsafe_since = 0.0
            logger.info("backlog failsafe OFF: pressure %.2f ≤ %.2f — captures resume",
                        pressure, b.resume_at)
        self._update_relief(now, pressure)
        return self.failsafe_paused

    def _update_relief(self, now: float, pressure: float) -> None:
        """Watch fewer streams while behind; give them back as soon as the queue is healthy.

        Bounded on purpose: it may never give up more than ``slots - relief_floor`` streams, so
        the board cannot empty itself no matter how long the PC stays busy."""
        b = self.settings.backlog
        if not b.relief:
            self.slot_relief = 0
            return
        ceiling = max(0, len(self.targets) - b.relief_floor)
        stuck = (self.failsafe_paused and self._failsafe_since
                 and now - self._failsafe_since >= b.relief_after_s)
        if stuck and self.slot_relief < ceiling and now - self._relief_at >= b.relief_after_s:
            self._relief_at = now
            self.slot_relief = min(ceiling, self.slot_relief + b.relief_step)
            logger.info("catching up: watching %d fewer streams for now", self.slot_relief)
        elif not self.failsafe_paused and self.slot_relief and pressure <= b.resume_at:
            self._relief_at = now
            self.slot_relief = max(0, self.slot_relief - b.relief_step)
            logger.info("caught up: back to watching %d more streams",
                        b.relief_step if self.slot_relief else b.relief_step)

    async def _refresh_failsafe(self) -> None:
        self._update_failsafe(self.pressure)
        await self._sync_capture_pause()

    async def _sync_capture_pause(self) -> None:
        async with self._pause_lock:
            want = self.paused
            if want and not self.captures_paused:
                self.captures_paused = True
                await self.captures.pause()
            elif not want and self.captures_paused:
                self.captures_paused = False
                self.captures.resume()

    async def pause(self) -> None:
        self.manual_paused = True
        logger.info("capture paused by user")
        await self._sync_capture_pause()

    async def resume(self) -> None:
        self.manual_paused = False
        logger.info("capture resumed by user")
        await self._sync_capture_pause()

    # ------------------------------------------------------------------ loops
    def _on_chat(self, key: str, text: str, t: float) -> None:
        self.detector.add_message(key, text, t)

    async def _discovery_loop(self) -> None:
        while True:
            try:
                self.discovery.boosted = {p: self.analytics.boosted_streamers(p)
                                          for p in ("twitch", "kick")}
                targets = await self.discovery.refresh()
                if self.slot_relief:      # behind: watch the strongest few, not everything
                    keep = max(self.settings.backlog.relief_floor, len(targets) - self.slot_relief)
                    targets = targets[:keep]
                await self._set_targets(targets)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let discovery die; the traceback is logged
                logger.exception("discovery loop error")
            self._discovery_wake.clear()
            try:
                await asyncio.wait_for(self._discovery_wake.wait(),
                                       self.settings.discovery.interval_s)
            except asyncio.TimeoutError:
                pass

    async def _set_targets(self, targets: list[StreamTarget]) -> None:
        now = time.time()
        new = {t.key: t for t in targets}
        for key in set(self.targets) - set(new):
            self.detector.remove(key)
            self._audio_seen.pop(key, None)
        for key in new:
            self.detector.track(key, now)
        self.targets = new
        await self.captures.set_targets(targets)
        await self.chat.set_targets(targets)

    async def _detector_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.detector.tick_s)
            now = time.time()
            for key, target in list(self.targets.items()):
                event = self.detector.evaluate(target, now)
                if event is None:
                    continue
                self.spikes_total += 1
                if self.paused:
                    self.spikes_ignored += 1
                    logger.info("spike on %s ignored (%s)", key,
                                "failsafe" if self.failsafe_paused else "paused")
                    continue
                if self.captures.get(key) is None:
                    self.spikes_ignored += 1
                    continue
                self._launch(Candidate(id=event.id, event=event, raw_path="",
                                       start_wall=event.t_wall - self.settings.clip.pre_s,
                                       end_wall=event.t_wall + self.settings.clip.post_s))

    async def _audio_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.detector.audio_poll_s)
            if self.captures_paused:
                continue
            for key, cap in list(self.captures.captures.items()):
                try:
                    res = await cap.latest_audio_level()
                except asyncio.CancelledError:
                    raise
                except (OSError, CmdTimeout) as exc:
                    logger.debug("audio level %s failed: %s", key, exc)
                    continue
                if not res:
                    continue
                name, level = res
                if level is not None and self._audio_seen.get(key) != name:
                    self._audio_seen[key] = name
                    self.detector.add_audio(key, level, time.time())

    async def _gpu_loop(self) -> None:
        exe = shutil.which("nvidia-smi")
        while True:
            if exe:
                try:
                    res = await run_cmd([exe, "--query-gpu=" + ",".join(q for q, _ in _GPU_FIELDS),
                                         "--format=csv,noheader,nounits"],
                                        self.settings.capture.probe_timeout_s)
                    parts = [p.strip() for p in res.out_text.splitlines()[0].split(",")]
                    if len(parts) != len(_GPU_FIELDS):
                        raise ValueError(f"unexpected nvidia-smi output: {res.out_text!r}")
                    self.gpu = {key: (val if key == "name" else float(val))
                                for (_, key), val in zip(_GPU_FIELDS, parts)}
                    self.clipability.vram_gb = self.copywriter.vram_gb = vram_gb(self.gpu)
                except (OSError, CmdTimeout, IndexError, ValueError) as exc:
                    logger.debug("nvidia-smi failed: %s", exc)
                    self.gpu = None
            await asyncio.sleep(self.settings.app.gpu_poll_s)

    async def _first_run_models(self) -> None:
        """Download the models this PC's judge profile needs (after the first GPU reading)."""
        if self.gpu is None:                       # let nvidia-smi report the card size first
            await asyncio.sleep(self.settings.app.gpu_poll_s)
        await self.model_setup.run(vram_gb(self.gpu))

    async def _housekeeping_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.capture.buffer_s)
            try:
                await asyncio.to_thread(self.captures.cleanup_retired)
                await self._check_disk()
                await self._refresh_failsafe()
            except OSError as exc:
                logger.warning("housekeeping: %s", exc)

    # ------------------------------------------------------------------ candidates
    def _launch(self, c: Candidate) -> None:
        task = asyncio.create_task(self._run_candidate(c), name=f"cand:{c.id}")
        self._inflight[c.id] = task
        self._stage_of[c.id] = c.stage
        task.add_done_callback(lambda _t, cid=c.id: self._done(cid))
        self._update_failsafe(self.pressure)
        asyncio.get_running_loop().create_task(self._sync_capture_pause())

    def _done(self, cid: str) -> None:
        self._inflight.pop(cid, None)
        self._stage_of.pop(cid, None)
        if self._running:
            self._update_failsafe(self.pressure)
            asyncio.get_running_loop().create_task(self._sync_capture_pause())

    async def _save(self, c: Candidate, stage: Stage | None = None) -> None:
        if stage is not None:
            c.stage = stage
            self._stage_of[c.id] = stage
        await asyncio.to_thread(self.store.upsert_candidate, c)

    async def _check_disk(self) -> None:
        """Keep the buffer drive usable: tidy up first, pause only if that was not enough.

        Pausing capture is the last resort - it stops the whole point of the app - so anything
        safe to delete goes first: work files, buffers for streams nobody watches, then the
        oldest buffered video."""
        free = await free_bytes(self.paths.buffer)
        if free is None:
            return
        want = self.settings.app.min_free_gb
        if free < want * GIB:
            await asyncio.to_thread(sweep, self.settings, self.paths, self._live_keys(), want)
            free = await free_bytes(self.paths.buffer) or free
        low = free < want * GIB
        if low != self.disk_paused:
            self.disk_paused = low
            logger.warning("disk space %s: %.1f GB free on the buffer drive — capture %s",
                           "LOW" if low else "ok", free / GIB, "paused" if low else "resumed")
            await self._sync_capture_pause()

    def _live_keys(self) -> set[str]:
        """Buffer folder names for the streams being watched right now."""
        return {safe_name(key) for key in self.targets}

    async def _abandon_stale(self) -> None:
        """Written off at startup: their buffered video is long gone, so they can never finish."""
        hours = self.settings.clip.abandon_after_h
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(hours=hours)).isoformat()
        dropped = await asyncio.to_thread(
            self.store.abandon_stale, UNFINISHED, cutoff,
            f"given up: still unfinished after {hours:g}h, and the buffer it came from is gone")
        if dropped:
            logger.info("cleared %d clip(s) that could never finish", dropped)

    async def _sweep_loop(self) -> None:
        """Clear out work files and dead buffers on a timer, so space never gets tight."""
        while True:
            minutes = self.settings.app.sweep_min
            if minutes <= 0:
                await asyncio.sleep(SWEEP_IDLE_S)
                continue
            await asyncio.sleep(minutes * 60)
            try:
                await asyncio.to_thread(sweep, self.settings, self.paths, self._live_keys())
            except OSError as exc:
                logger.warning("tidy-up skipped: %s", exc)

    async def _discard_raw(self, c: Candidate) -> None:
        if c.raw_path and not self.settings.edit.keep_raw:
            await asyncio.to_thread(Path(c.raw_path).unlink, True)

    async def _clear_work(self, c: Candidate) -> None:
        """Everything this candidate left in the work folder, gone as soon as it is finished.

        Frames and the concat list are only useful while the AI is looking at the clip, so they
        always go. The source cut goes too unless ``edit.keep_raw`` says to hang on to it."""
        def wipe() -> None:
            shutil.rmtree(frames_dir(self.paths, c.id), ignore_errors=True)
            for leftover in self.paths.work.glob(f"{c.id}*"):
                if leftover.is_dir():
                    shutil.rmtree(leftover, ignore_errors=True)
                elif leftover.suffix.lower() != RAW_SUFFIX or not self.settings.edit.keep_raw:
                    leftover.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(wipe)
        except OSError as exc:
            logger.debug("could not clear work files for %s: %s", c.id, exc)

    async def _finish(self, c: Candidate, stage: Stage, error: str) -> None:
        c.error = error
        await self._save(c, stage)
        if not self.settings.clip.keep_rejected_hours:   # else kept for preview, cleaned up later
            await self._discard_raw(c)
        await self._clear_work(c)
        logger.info("candidate %s %s: %s", c.id, stage, error)

    async def _run_candidate(self, c: Candidate) -> None:
        s = self.settings
        try:
            if not c.raw_path or not Path(c.raw_path).exists():
                await self._save(c, Stage.QUEUED)
                cap = self.captures.get(c.event.target.key)
                if cap is None:
                    await self._finish(c, Stage.FAILED, "capture no longer running")
                    return
                async with self._assemble_limit:
                    cut = await assemble(c.event, cap, s, self.paths.work)
                c.raw_path, c.start_wall, c.end_wall = cut.raw_path, cut.start_wall, cut.end_wall
            await self._save(c, Stage.QC)
            expected = c.end_wall - c.start_wall
            qc = await quality_check(Path(c.raw_path), expected, s)
            if not qc.ok:
                await self._finish(c, Stage.REJECTED, f"qc: {qc.reason}")
                return
            duration = qc.duration or expected

            await self._save(c, Stage.TRANSCRIBE)
            tr = await self.transcriber.transcribe(Path(c.raw_path))
            c.transcript, c.words = tr.text, tr.words
            said = Filter(s).slurs_in(c.transcript) if s.safety.enabled and s.safety.skip_slur_clips else []
            if said:
                await self._finish(c, Stage.REJECTED, f"unsafe: slur in speech ({len(said)}x)")
                return

            if looks_like_dead_air(c, s.ai):
                await self._finish(c, Stage.REJECTED,
                                   "dead_air: nobody spoke, the sound never jumped and no keyword "
                                   "fired (skipped before the AI)")
                return

            # one clip at a time through vision + judge + writer: frames are grabbed only when it's
            # this clip's turn (a restart with a big backlog used to launch hundreds of ffmpegs)
            async with self._ai_limit:
                try:
                    await asyncio.wait_for(self._think(c, duration),
                                           timeout=s.ai.stage_deadline_s)
                except asyncio.TimeoutError:
                    await self._finish(c, Stage.REJECTED,
                                       "gave up: the AI stage passed its deadline, so the "
                                       "queue moved on")
                    return
                if not c.verdict or not c.verdict.get("passed"):
                    return                       # _think already recorded why
            await self._save(c, Stage.EDIT)
            out = clips_target(self.settings, self.paths) / f"{c.event.target.login}_{c.id}.mp4"
            facecam = await self._auto_facecam(c)
            async with self._edit_limit:
                await render(Path(c.raw_path), out, verdict["trim_start"], verdict["trim_end"],
                             c.words, c.event.target.login, self.settings, self.paths.work,
                             self.settings.brand.watermark, hook=c.post_copy.get("hook", ""),
                             spike_at=verdict.get("peak_at", c.event.t_wall - c.start_wall),
                             facecam_auto=facecam)
            c.final_path = str(out)
            await self._save(c, Stage.SCHEDULED)
            await self._discard_raw(c)
            await self._clear_work(c)
            logger.info("candidate %s ready: %s (score %s)", c.id, verdict["title"],
                        verdict["score"])
        except asyncio.CancelledError:
            raise
        except (AssembleError, RenderError, CmdTimeout, OSError, RuntimeError, ValueError) as exc:
            logger.warning("candidate %s failed: %s", c.id, exc)
            await self._finish(c, Stage.FAILED, str(exc) or type(exc).__name__)

    async def _think(self, c: Candidate, duration: float) -> None:
        """Watch, judge and write one clip.

        Only one clip is in here at a time, so the caller holds it to a deadline: a clip that
        hangs must not stop every other clip behind it."""
        s = self.settings
        await self._save(c, Stage.CLIPABILITY)
        while True:
            if s.ai.vision_enabled and not c.visual:
                seen = await self.vision.describe(
                    Path(c.raw_path), duration, c.event.t_wall - c.start_wall,
                    c.event.target.display_name, c.event.target.category,
                    save_dir=frames_dir(self.paths, c.id))
                c.visual, c.frame_times = seen.text, seen.times or []
            imported = c.source.get("type") == "twitch_clip"
            if imported and not s.clip_import.judge:
                verdict = {"verdict": "pass", "category": "moment", "score": SCORE_MAX,
                           "reason": "popular Twitch clip (judging off for imports)",
                           "title": c.event.target.title, "caption": "", "hashtags": [],
                           "trim_start": 0.0, "trim_end": duration, "passed": True}
                break
            try:
                verdict = await self.clipability.judge(
                    c, duration, s.clip_import.min_score if imported else None)
                break
            except AIUnavailable as exc:
                c.error = f"waiting for Ollama: {exc}"
                await self._save(c)
                await asyncio.sleep(self.settings.ai.unavailable_retry_s)
        verdict = await self._second_look(c, verdict, duration)
        c.verdict, c.error = verdict, ""
        if not verdict["passed"]:
            await self._finish(c, Stage.REJECTED, f"{verdict['category']}: {verdict['reason']}")
            return

        c.post_copy = await self.copywriter.write(c, self.analytics.notes(), Path(c.raw_path))

    async def _second_look(self, c, verdict: dict, duration: float) -> dict:
        """A near-miss is worth a second opinion: look at more frames and judge once more.

        The judge is strict on purpose, but a single thin description makes it throw away real
        moments (a good line over static gameplay, a reaction the frames missed). Only clips that
        land within ``ai.second_look_margin`` of the pass mark pay for this."""
        s = self.settings
        pass_mark = s.ai.min_score
        if (not s.ai.second_look or verdict["passed"] or not s.ai.vision_enabled
                or verdict["category"] == "keyword_false"
                or verdict["score"] < pass_mark - s.ai.second_look_margin):
            return verdict
        try:
            seen = await self.vision.describe(
                Path(c.raw_path), duration, c.event.t_wall - c.start_wall,
                c.event.target.display_name, c.event.target.category,
                save_dir=frames_dir(self.paths, c.id), frames=s.ai.second_look_frames)
            if not seen.text:
                return verdict
            c.visual, c.frame_times = seen.text, seen.times or []
            second = await self.clipability.judge(c, duration)
        except (AIUnavailable, OSError) as exc:
            logger.debug("second look on %s skipped: %s", c.id, exc)
            return verdict
        logger.info("candidate %s second look: %s -> %s", c.id, verdict["score"], second["score"])
        return second if second["score"] >= verdict["score"] else verdict

    async def _auto_facecam(self, c: Candidate) -> list[int] | None:
        """Webcam box for this streamer: cached, else found by the vision model (best effort)."""
        e = self.settings.edit
        login = c.event.target.login.lower()
        if (e.layout != "facecam" or not e.auto_facecam or not self.settings.ai.vision_enabled
                or login in e.facecam):
            return None
        cache_file = self.paths.data / "facecams.json"
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
        except (json.JSONDecodeError, OSError):
            cache = {}
        hit = cache.get(login)
        if hit and time.time() - hit.get("at", 0) < e.auto_facecam_refresh_h * 3600:
            return hit.get("box")
        frame = frames_dir(self.paths, c.id) / "0.jpg"
        try:
            if not frame.exists():
                return None
            info = await probe_media(Path(c.raw_path), self.settings)
            box = await find_facecam(self.vision, await asyncio.to_thread(frame.read_bytes),
                                     info.width, info.height)
        except (RuntimeError, RenderError, OSError, CmdTimeout) as exc:
            logger.info("facecam finder for %s: %s", login, exc)
            return None
        cache[login] = {"box": box, "at": time.time()}
        await asyncio.to_thread(atomic_write, cache_file, json.dumps(cache, indent=2))
        logger.info("facecam for %s: %s", login, box or "none found")
        return box

    async def _recover(self) -> None:
        """Resume candidates interrupted by the last shutdown."""
        for c in await asyncio.to_thread(self.store.active_candidates):
            if c.raw_path and Path(c.raw_path).exists():
                logger.info("resuming candidate %s from %s", c.id, c.stage)
                self._launch(c)
            else:
                c.error = "interrupted by restart before the cut was saved"
                await self._save(c, Stage.FAILED)

    async def submit_event(self, event: SpikeEvent) -> str:
        """Inject a spike (used by tests and the dashboard's manual clip button)."""
        c = Candidate(id=event.id, event=event, raw_path="",
                      start_wall=event.t_wall - self.settings.clip.pre_s,
                      end_wall=event.t_wall + self.settings.clip.post_s)
        self._launch(c)
        return c.id

    # ------------------------------------------------------------------ dashboard
    async def snapshot(self) -> dict:
        s = self.settings
        monitors = []
        for key, t in self.targets.items():
            cap = self.captures.get(key)
            monitors.append({
                "key": key, "platform": str(t.platform), "login": t.login,
                "display_name": t.display_name, "category": t.category, "viewers": t.viewers,
                "title": t.title, "forced": t.forced, "url": t.url,
                "capture": cap.snapshot() if cap else None,
                "chat": self.detector.stats(key), "no_chat": key in self.chat.no_chat})
        since = time.time() - s.dashboard.stage_window_h * 3600
        counts = await asyncio.to_thread(self.store.stage_counts, since)
        live = Counter(str(st) for st in self._stage_of.values())
        samples = await asyncio.to_thread(self.store.recent_candidates, s.dashboard.samples_limit)
        posts = await asyncio.to_thread(self.store.recent_posts, s.dashboard.posts_limit)
        status = "paused" if self.manual_paused else "failsafe" if self.failsafe_paused else (
            "disk" if self.disk_paused else None) or (
            "running" if self.targets else "idle")
        return {
            "version": __version__, "status": status, "started_at": self.started_at,
            "manual_paused": self.manual_paused, "failsafe": self.failsafe_paused,
            "backlog": {"in_flight": self.in_flight, "capacity": s.backlog.capacity,
                        "pressure": round(self.pressure, 3), "pause_at": s.backlog.pause_at,
                        "resume_at": s.backlog.resume_at},
            "gpu": self.gpu, "monitors": monitors,
            "stages": {"window_h": s.dashboard.stage_window_h, "counts": counts,
                       "live": dict(live)},
            "spikes": {"total": self.spikes_total, "ignored": self.spikes_ignored},
            "samples": samples, "posts": posts,
            "scheduler": await self.scheduler.snapshot(),
            "discovery": {"messages": ({"setup": self.blocked} if self.blocked else {})
                          | self.discovery.messages,
                          "last_refresh": self.discovery.last_refresh},
            "chat": self.chat.snapshot(),
            "ai": {"busy": self.clipability.busy, "error": self.clipability.last_error,
                   "model": self.clipability.model(), "profile": self.clipability.plan().profile,
                   "vision_enabled": s.ai.vision_enabled,
                   "vision_model": s.ai.vision_model, "vision_busy": self.vision.busy,
                   "vision_error": self.vision.last_error},
            "whisper": {"status": self.transcriber.status, "jobs": self.transcriber.jobs},
            "copywriter": {"busy": self.copywriter.busy, "error": self.copywriter.last_error},
            "analytics": self.analytics.snapshot(),
            "clip_import": self.importer.snapshot(),
            "children": child_count(),
            "oauth": {p: self.oauth.connected(p) for p in ("youtube", "tiktok")},
            "model_setup": self.model_setup.status if hasattr(self, "model_setup") else {},
            "clips_dir": str(self.paths.clips),
        }

    async def publish_now(self, cid: str, platforms: list[str]) -> dict:
        """Render the clip if it never was (e.g. a rejected one), then post it immediately."""
        c = await asyncio.to_thread(self.store.get_candidate, cid)
        if c is None:
            return {"ok": False, "error": "clip not found"}
        if not (c.final_path and Path(c.final_path).exists()):
            if not (c.raw_path and Path(c.raw_path).exists()):
                return {"ok": False, "error": "this clip's video was already cleaned up"}
            info = await probe_media(Path(c.raw_path), self.settings)
            v = c.verdict or {}
            start = float(v.get("trim_start", 0.0) or 0.0)
            end = float(v.get("trim_end", 0.0) or 0.0) or info.duration
            out = self.paths.clips / f"{c.event.target.login}_{c.id}.mp4"
            async with self._edit_limit:
                await render(Path(c.raw_path), out, start, min(end, info.duration), c.words,
                             c.event.target.login, self.settings, self.paths.work,
                             self.settings.brand.watermark, hook=c.post_copy.get("hook", ""),
                             spike_at=c.event.t_wall - c.start_wall)
            c.final_path = str(out)
            if not v.get("title"):
                c.verdict = {**v, "title": c.event.target.title or c.event.target.display_name}
            if c.stage in (Stage.REJECTED, Stage.FAILED):
                c.stage = Stage.SCHEDULED
            await self._save(c)
        results = await self.scheduler.publish_now(cid, platforms)
        return {"ok": any(r["ok"] for r in results.values()), "results": results}

    # ------------------------------------------------------------------ studio
    def studio_preview_path(self, cid: str) -> Path:
        return self.paths.work / "studio" / f"{cid}.mp4"

    async def studio_clip(self, cid: str) -> dict | None:
        """What the Studio needs to open a clip: its source length, trim, hook and words."""
        c = await asyncio.to_thread(self.store.get_candidate, cid)
        if c is None:
            return None
        has_source = bool(c.raw_path and Path(c.raw_path).exists())
        duration = (await probe_media(Path(c.raw_path), self.settings)).duration if has_source else 0.0
        v = c.verdict or {}
        return {"id": c.id, "has_source": has_source, "duration": duration,
                "trim_start": float(v.get("trim_start", 0.0) or 0.0),
                "trim_end": float(v.get("trim_end", 0.0) or 0.0) or duration,
                "hook": c.post_copy.get("hook", ""), "title": v.get("title", ""),
                "streamer": c.event.target.display_name, "style": c.post_copy.get("style", {}),
                "words": [{"t": w.start, "w": w.text} for w in c.words],
                "rendered": bool(c.final_path and Path(c.final_path).exists())}

    async def studio_render(self, cid: str, style: dict, trim_start: float, trim_end: float,
                            hook: str, apply: bool) -> dict:
        """Render ``cid`` with a Studio style. Preview = fast encode into work/studio;
        apply = full-quality re-render of the real clip, saving trim, hook and style."""
        c = await asyncio.to_thread(self.store.get_candidate, cid)
        if c is None:
            return {"ok": False, "error": "clip not found"}
        if not (c.raw_path and Path(c.raw_path).exists()):
            return {"ok": False, "error": "this clip's source cut is gone, so it can't be restyled. "
                                          "Turn on 'Keep source cuts' and new clips will be editable."}
        settings = apply_style(self.settings, style)            # ValidationError -> caller
        if not apply:
            settings = merge_incoming(settings, {"edit": {"preset": self.settings.studio.preview_preset,
                                                          "crf": self.settings.studio.preview_crf}})
        info = await probe_media(Path(c.raw_path), self.settings)
        start = max(0.0, min(float(trim_start), info.duration))
        end = min(info.duration, max(float(trim_end), start + self.settings.clip.min_len_s / 2))
        if apply:
            out = self.paths.clips / f"{c.event.target.login}_{c.id}.mp4"
            tmp = out.with_suffix(".studio.mp4")
        else:
            out = tmp = self.studio_preview_path(cid)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        async with self._edit_limit:
            await render(Path(c.raw_path), tmp, start, end, c.words, c.event.target.login,
                         settings, self.paths.work, self.settings.brand.watermark, hook=hook,
                         spike_at=c.event.t_wall - c.start_wall)
        if apply:
            await asyncio.to_thread(tmp.replace, out)
            c.final_path = str(out)
            c.verdict = {**(c.verdict or {}), "trim_start": round(start, 2), "trim_end": round(end, 2)}
            c.post_copy = {**c.post_copy, "hook": hook, "style": clean_style(style)}
            if c.stage in (Stage.REJECTED, Stage.FAILED):
                c.stage = Stage.SCHEDULED
            await self._save(c)
        stamp = int(time.time())
        return {"ok": True, "url": f"/media/{cid}?t={stamp}" if apply else f"/media/studio/{cid}.mp4?t={stamp}"}

    def candidate_media(self, cid: str) -> Path | None:
        c = self.store.get_candidate(cid)
        if c is None:
            return None
        for p in (c.final_path, c.raw_path):
            if p and Path(p).exists():
                return Path(p)
        return None

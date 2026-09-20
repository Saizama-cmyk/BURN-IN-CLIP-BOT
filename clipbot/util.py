"""Shared helpers: child-process management (Windows job object), backoff, short commands."""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import subprocess
import sys
from dataclasses import dataclass

logger = logging.getLogger("clipbot.util")

# How much of an API error body is kept in log lines / error messages (display only).
ERR_SNIPPET = 300

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_children: set[asyncio.subprocess.Process] = set()
_popen_children: set[subprocess.Popen] = set()
_job_handle = None


# --------------------------------------------------------------------------- job object
def _init_job() -> None:
    """Put every child in a job that is killed when BURN-IN exits, even on a crash."""
    global _job_handle
    if _job_handle is not None or os.name != "nt":
        return
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        logger.warning("CreateJobObject failed (%s); children rely on explicit cleanup",
                       ctypes.get_last_error())
        return

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x800      # lets "Restart BURN-IN" relaunch outlive us
    JobObjectExtendedLimitInformation = 9
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                                             | JOB_OBJECT_LIMIT_BREAKAWAY_OK)
    if not k32.SetInformationJobObject(wintypes.HANDLE(job), JobObjectExtendedLimitInformation,
                                       ctypes.byref(info), ctypes.sizeof(info)):
        logger.warning("SetInformationJobObject failed (%s)", ctypes.get_last_error())
        return
    _job_handle = job


def _assign(pid: int) -> None:
    if os.name != "nt":
        return
    _init_job()
    if _job_handle is None:
        return
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    PROCESS_SET_QUOTA_TERMINATE = 0x0100 | 0x0001
    h = k32.OpenProcess(PROCESS_SET_QUOTA_TERMINATE, False, pid)
    if not h:
        return
    try:
        if not k32.AssignProcessToJobObject(wintypes.HANDLE(_job_handle), wintypes.HANDLE(h)):
            logger.debug("AssignProcessToJobObject(%s) failed: %s", pid, ctypes.get_last_error())
    finally:
        k32.CloseHandle(wintypes.HANDLE(h))


# --------------------------------------------------------------------------- spawning
def no_window_flags() -> int:
    return _CREATE_NO_WINDOW if os.name == "nt" else 0


async def spawn(*args: str, **kwargs) -> asyncio.subprocess.Process:
    """create_subprocess_exec that hides console windows and tracks the child."""
    kwargs.setdefault("creationflags", no_window_flags())
    proc = await asyncio.create_subprocess_exec(*args, **kwargs)
    _children.add(proc)
    _assign(proc.pid)
    return proc


def popen(args: list[str], **kwargs) -> subprocess.Popen:
    """subprocess.Popen twin of spawn() for code that must use raw OS pipes."""
    kwargs.setdefault("creationflags", no_window_flags())
    proc = subprocess.Popen(args, **kwargs)
    _popen_children.add(proc)
    _assign(proc.pid)
    return proc


def forget(proc) -> None:
    _children.discard(proc)
    _popen_children.discard(proc)


async def terminate(proc, grace_s: float) -> None:
    """Ask a child to stop, kill it after ``grace_s``."""
    if isinstance(proc, subprocess.Popen):
        if proc.poll() is None:
            proc.terminate()
            try:
                await asyncio.to_thread(proc.wait, grace_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                await asyncio.to_thread(proc.wait)
    elif proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), grace_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
    forget(proc)


async def terminate_all(grace_s: float) -> None:
    procs = list(_children) + list(_popen_children)
    if procs:
        logger.info("stopping %d child process(es)", len(procs))
    await asyncio.gather(*(terminate(p, grace_s) for p in procs), return_exceptions=True)


def kill_all_sync() -> None:
    """Last-resort synchronous kill (used from atexit / tray Quit when the loop is gone)."""
    for p in list(_popen_children):
        if p.poll() is None:
            p.kill()
    for p in list(_children):
        if p.returncode is None:
            try:
                p.kill()
            except ProcessLookupError:
                pass
    _children.clear()
    _popen_children.clear()


def child_count() -> int:
    return sum(1 for p in _children if p.returncode is None) + sum(
        1 for p in _popen_children if p.poll() is None)


@dataclass
class CmdResult:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def err_text(self) -> str:
        return self.stderr.decode("utf-8", "replace")

    @property
    def out_text(self) -> str:
        return self.stdout.decode("utf-8", "replace")


class CmdTimeout(RuntimeError):
    pass


async def run_cmd(args: list[str], timeout_s: float, cwd: str | None = None,
                  grace_s: float = 0.0) -> CmdResult:
    """Run a short command to completion; kill it on timeout or cancellation."""
    proc = await spawn(*args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                       stderr=asyncio.subprocess.PIPE, cwd=cwd)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError as exc:
        await terminate(proc, grace_s)
        raise CmdTimeout(f"{os.path.basename(args[0])} timed out after {timeout_s:.0f}s") from exc
    except asyncio.CancelledError:
        await terminate(proc, grace_s)
        raise
    finally:
        forget(proc)
    return CmdResult(proc.returncode or 0, out, err)


class Backoff:
    """Exponential backoff between ``lo`` and ``hi`` seconds (doubles each failure)."""

    def __init__(self, lo: float, hi: float) -> None:
        self.lo, self.hi = lo, hi
        self.current = lo

    def next(self) -> float:
        delay = self.current
        self.current = min(self.hi, self.current * 2)
        return delay

    def reset(self) -> None:
        self.current = self.lo


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))

"""Encryption at rest for secrets, tied to the signed-in Windows account (DPAPI).

Values are stored as ``dpapi:<base64>``. Only the same Windows user on the same PC can
decrypt them, so copying settings.json or tokens.json to another machine or account exposes
nothing. Plain values (older files) are still read and get encrypted on the next save.
"""
from __future__ import annotations

import base64
import ctypes
import logging
import os
from ctypes import wintypes

logger = logging.getLogger("clipbot.secure")

PREFIX = "dpapi:"
CRYPTPROTECT_UI_FORBIDDEN = 0x01
_ENTROPY = b"Ashvane secrets v1"   # app-specific salt so other DPAPI blobs can't be swapped in
_LEGACY_ENTROPY = (b"BURN-IN secrets v1",)   # values saved before the rename still open


class SecureError(RuntimeError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def available() -> bool:
    return os.name == "nt"


def _call(fn_name: str, data: bytes, entropy: bytes = _ENTROPY) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    src, _keep = _blob(data)
    ent, _keep2 = _blob(entropy)
    out = _Blob()
    fn = getattr(crypt32, fn_name)
    ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise SecureError(f"{fn_name} failed (Windows error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(text: str) -> str:
    """Encrypt ``text`` for this Windows user. Empty stays empty; already-protected stays."""
    if not text or text.startswith(PREFIX) or not available():
        return text
    return PREFIX + base64.b64encode(_call("CryptProtectData", text.encode("utf-8"))).decode("ascii")


def unprotect(text: str) -> str:
    """Decrypt a ``dpapi:`` value; plain values pass through unchanged."""
    if not isinstance(text, str) or not text.startswith(PREFIX):
        return text
    if not available():
        raise SecureError("encrypted secret can only be read on Windows")
    try:
        raw = base64.b64decode(text[len(PREFIX):])
    except ValueError as exc:
        raise SecureError(f"corrupt encrypted value: {exc}") from exc
    for entropy in (_ENTROPY, *_LEGACY_ENTROPY):
        try:
            return _call("CryptUnprotectData", raw, entropy).decode("utf-8")
        except SecureError:
            if entropy == _LEGACY_ENTROPY[-1]:
                raise
    raise SecureError("unreachable")

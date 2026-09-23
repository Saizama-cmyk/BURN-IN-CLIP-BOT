import base64

import pytest

from clipbot import secure

pytestmark = pytest.mark.skipif(not secure.available(), reason="DPAPI is Windows-only")


def test_roundtrip():
    sealed = secure.protect("hunter2-not-real")
    assert sealed.startswith(secure.PREFIX) and "hunter2" not in sealed
    assert secure.unprotect(sealed) == "hunter2-not-real"


def test_secret_saved_under_the_old_name_still_opens():
    """Keys encrypted before the rename used the old salt; they must keep working."""
    raw = secure._call("CryptProtectData", b"old-key", secure._LEGACY_ENTROPY[0])
    sealed = secure.PREFIX + base64.b64encode(raw).decode("ascii")
    assert secure.unprotect(sealed) == "old-key"


def test_a_foreign_blob_is_refused():
    raw = secure._call("CryptProtectData", b"x", b"some other app")
    with pytest.raises(secure.SecureError):
        secure.unprotect(secure.PREFIX + base64.b64encode(raw).decode("ascii"))

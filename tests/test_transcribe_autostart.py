import asyncio
import os
from types import SimpleNamespace

import pytest

from clipbot import autostart
from clipbot.config import Settings
from clipbot.transcribe import Transcriber, format_transcript


def test_format_transcript():
    assert format_transcript([(0, 1.25, " hi "), (2, 3, "  "), (3, 4.5, "there")]) == \
        "[0.0-1.2] hi\n[3.0-4.5] there"


class FakeModel:
    def __init__(self, device, fail_first=False):
        self.device = device
        self.fail_first = fail_first

    def transcribe(self, path, **kw):
        if self.fail_first:
            raise RuntimeError("Library cublas64_12.dll is not found")
        words = [SimpleNamespace(word=" hey", start=0.1, end=0.4)]
        seg = SimpleNamespace(start=0.0, end=1.0, text="hey", words=words)
        return iter([seg]), SimpleNamespace(language="en")


def test_cuda_failure_on_first_inference_falls_back_to_cpu(monkeypatch):
    t = Transcriber(Settings())
    built = []

    def build(device, compute):
        built.append((device, compute))
        return FakeModel(device, fail_first=(device == "cuda"))
    monkeypatch.setattr(t, "_build", build)
    out = asyncio.run(t.transcribe("x.mp4"))
    assert built == [("cuda", "int8_float16"), ("cpu", "int8")]
    assert out.device == "cpu" and out.words[0].text == "hey"
    assert "CUDA failed" in t.status


def test_cuda_failure_at_load_falls_back(monkeypatch):
    t = Transcriber(Settings())

    def build(device, compute):
        if device == "cuda":
            raise RuntimeError("CUDA driver version is insufficient")
        return FakeModel(device)
    monkeypatch.setattr(t, "_build", build)
    assert asyncio.run(t.transcribe("x.mp4")).device == "cpu"


def test_errors_after_verified_cuda_are_not_swallowed(monkeypatch):
    t = Transcriber(Settings())
    model = FakeModel("cuda")
    monkeypatch.setattr(t, "_build", lambda d, c: model)
    asyncio.run(t.transcribe("a.mp4"))
    model.fail_first = True
    with pytest.raises(RuntimeError):
        asyncio.run(t.transcribe("b.mp4"))


@pytest.mark.skipif(os.name != "nt", reason="Windows registry")
def test_run_key_add_remove():
    name = "ClipBotPytest"
    try:
        autostart.set_autostart(True, name=name, command='"C:\\x.exe" --autostart')
        assert autostart.get_autostart(name) == '"C:\\x.exe" --autostart'
        autostart.set_autostart(False, name=name)
        assert autostart.get_autostart(name) is None
        autostart.set_autostart(False, name=name)          # idempotent
    finally:
        autostart.set_autostart(False, name=name)


def test_launch_command_dev():
    cmd = autostart.launch_command()
    assert "__main__.py" in cmd and "--autostart" in cmd and "--window" in cmd


def test_signin_launch_is_silent_by_default():
    from clipbot.__main__ import autostart_hidden, parse_args
    from clipbot.config import Settings, merge_incoming
    s = Settings()
    assert autostart_hidden(parse_args(["--window", "--autostart"]), s)
    assert not autostart_hidden(parse_args(["--window"]), s)
    loud = merge_incoming(s, {"app": {"autostart_silent": False}})
    assert not autostart_hidden(parse_args(["--window", "--autostart"]), loud)
    assert autostart_hidden(parse_args(["--window", "--minimized"]), loud)

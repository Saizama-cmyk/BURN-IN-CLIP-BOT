# ClipBot — acceptance gates

Every gate is one observable outcome with the exact command and the expected output.
Commands run from the repo folder in Git Bash unless noted; `PY=.venv/Scripts/python.exe`.
Gates that start the app use an isolated data folder via `CLIPBOT_HOME` so a developer's real
settings are never touched (the exe/installer gates use the real `%LOCALAPPDATA%\ClipBot`).

| # | Gate | Command | Expected |
|---|------|---------|----------|
| G1 | Test suite green | `$PY -m pytest -q` | last line `N passed` with 0 failed / 0 errors |
| G2 | App boots, state is JSON | `CLIPBOT_HOME=$TMP/cb $PY -m clipbot &` then `curl -s http://127.0.0.1:8787/api/state \| $PY -c "import json,sys;d=json.load(sys.stdin);print(sorted(d)[:5])"` | a list of top-level keys (e.g. `['backlog', 'captures', ...]`), exit 0 |
| G3 | Settings round-trip with masking | `$PY scripts/gate_settings.py` (PUT a changed value + a secret, GET it back, PUT the masked placeholder, GET again) | `SETTINGS ROUNDTRIP OK changed=… secret_masked=True masked_ignored=True` |
| G4 | Every config field is rendered | `$PY scripts/gate_form.py` (counts leaf fields in `/api/settings/schema`, loads the dashboard in headless Edge and counts `[data-field]` inputs) | `FORM FIELDS schema=N rendered=N` with equal numbers |
| G5 | No hard-coded tunables | `$PY -m pytest -q tests/test_no_hardcoded.py` | `N passed` |
| G6 | Editor renders 1080x1920 with audio + captions | `$PY -m pytest -q tests/test_editor.py` (lavfi testsrc+sine → `render()` → ffprobe) | `N passed`; the test asserts `width=1080 height=1920`, an `aac` audio stream, and that the ASS captions burned in (caption-region pixels differ from an uncaptioned render) |
| G7 | Detector fires on spike, quiet on flat chat | `$PY -m pytest -q tests/test_detector.py` | `N passed` |
| G8 | Backlog failsafe hysteresis | `$PY -m pytest -q tests/test_backlog.py` | `N passed` (pauses at 0.8, still paused at 0.6, resumes at 0.5) |
| G9 | Clipability JSON parse / retry / reject | `$PY -m pytest -q tests/test_clipability.py` | `N passed` (valid JSON parsed; malformed twice → one retry → `model_error` reject; Ollama down → `AIUnavailable`) |
| G10 | Live Ollama clipability call | `$PY scripts/live_clipability.py` | `LIVE VERDICT {...}` with a `verdict`, `score`, `category` from the real `qwen3-coder:30b` (skipped with `OLLAMA UNREACHABLE` only if Ollama is down) |
| G11 | build.bat produces the exe | `cmd //c build.bat` | `dist\ClipBot\ClipBot.exe` exists; bat prints `BUILD OK` |
| G12 | Built exe serves and exits cleanly | `$PY scripts/gate_exe.py` (launch exe `--headless`, poll `/api/state`, POST `/api/control/quit`, wait) | `EXE OK state_keys=… exit_code=0` and no leftover `ffmpeg`/`streamlink` children |
| G13 | install.bat creates both shortcuts | `cmd //c "install.bat /quiet"` then `ls` the two `.lnk` paths the installer prints (Desktop may be OneDrive-redirected) | both paths listed; `%LOCALAPPDATA%\Programs\ClipBot\ClipBot.exe` exists |
| G14 | Start-with-Windows toggles the Run key live | `$PY scripts/gate_runkey.py` (PUT `app.start_with_windows=true` on a running instance, `reg query`, PUT false, `reg query`) | `RUNKEY OK added=True removed=True` |
| G15 | Setup checklist on first run | `$PY scripts/gate_checklist.py` (fresh `CLIPBOT_HOME`, GET `/api/setup`) | `CHECKLIST OK` and a list of missing items, each with a `field` link such as `twitch.client_id` |
| G16 | Graceful failure without API keys | part of G2 run: `curl -s /api/state` → `discovery.messages` | contains `Twitch: no client ID/secret` style messages, no traceback in the log |
| G17 | Live end-to-end on a real stream (no keys) | `$PY scripts/e2e_live.py xqc kaicenat jynxzi` | `E2E RESULT {...}` with `stage` `scheduled` or `rejected` and a non-empty Qwen `verdict` |

#!/usr/bin/env bash
# Rerun every gate in GATES.md from scratch and print one evidence line per gate.
cd "$(dirname "$0")/.." || exit 1
PY=.venv/Scripts/python.exe
ev() { echo "EVIDENCE $1: $2"; }

ev G1 "$($PY -m pytest -q 2>&1 | tail -1)"

export CLIPBOT_HOME="$(mktemp -d)"
$PY -m clipbot --headless >/dev/null 2>&1 &
for _ in $(seq 60); do curl -s -o /dev/null http://127.0.0.1:8787/api/state && break; sleep 1; done
ev G2 "$(curl -s http://127.0.0.1:8787/api/state | $PY -c "import json,sys;d=json.load(sys.stdin);print(sorted(d)[:6], 'status=' + d['status'])")"
ev G16 "$(curl -s http://127.0.0.1:8787/api/state | $PY -c "import json,sys;print(json.load(sys.stdin)['discovery']['messages'])") traceback_in_log=$(grep -c Traceback "$CLIPBOT_HOME/logs/clipbot.log")"
curl -s -X POST -H "X-ClipBot: 1" http://127.0.0.1:8787/api/control/quit >/dev/null; sleep 6
unset CLIPBOT_HOME

ev G3 "$($PY scripts/gate_settings.py 2>&1 | tail -1)"
ev G4 "$($PY scripts/gate_form.py 2>&1 | tail -1)"
ev G5 "$($PY -m pytest -q tests/test_no_hardcoded.py 2>&1 | tail -1)"
ev G6 "$($PY -m pytest -q tests/test_editor.py 2>&1 | tail -1)"
ev G7 "$($PY -m pytest -q tests/test_detector.py 2>&1 | tail -1)"
ev G8 "$($PY -m pytest -q tests/test_backlog.py 2>&1 | tail -1)"
ev G9 "$($PY -m pytest -q tests/test_clipability.py 2>&1 | tail -1)"
ev G10 "$($PY scripts/live_clipability.py 2>&1 | tail -1)"
rm -rf dist build
ev G11 "$(cmd //c "$(cygpath -w "$PWD")\\build.bat" 2>&1 | tail -1) exe_exists=$(test -f dist/ClipBot/ClipBot.exe && echo yes || echo no)"
ev G12 "$($PY scripts/gate_exe.py 2>&1 | tail -1)"
DESK="$(powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')" | tr -d '\r')"
ev G13 "$(cmd //c "$(cygpath -w "$PWD")\\install.bat /quiet" 2>&1 | grep 'INSTALL OK') | $(ls "$APPDATA/Microsoft/Windows/Start Menu/Programs/ClipBot.lnk" "$DESK/ClipBot.lnk" 2>&1 | tr '\n' ' ')"
ev G14 "$($PY scripts/gate_runkey.py 2>&1 | tail -1)"
ev G15 "$($PY scripts/gate_checklist.py 2>&1 | grep CHECKLIST)"
ev G17 "$($PY scripts/e2e_live.py xqc kaicenat jynxzi zackrawrr caseoh_ 2>&1 | grep -a 'E2E RESULT' | cut -c1-600)"
echo ALL GATES RUN

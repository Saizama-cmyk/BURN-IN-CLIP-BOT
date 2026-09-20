"""G12: the built exe launches, serves /api/state, exits cleanly and leaves no children."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import httpx
from _gatelib import CLIENT, BASE, ROOT, fresh_home, out, start, stop

EXE = ROOT / "dist" / "ClipBot" / "ClipBot.exe"


def children(pid: int) -> list[str]:
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq %d } | "
          "ForEach-Object { $_.Name }" % pid)
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                         text=True)
    return [x for x in res.stdout.split() if x]


def main() -> int:
    if not EXE.exists():
        out("FAILED: dist/ClipBot/ClipBot.exe missing (run build.bat)")
        return 1
    proc = start([str(EXE), "--headless"], fresh_home("exe"))
    state = CLIENT.get("/api/state", timeout=30).json()
    keys = sorted(state)[:6]
    pid = proc.pid
    code = stop(proc)
    left = children(pid)
    ok = code == 0 and not left and state.get("version")
    out(f"EXE {'OK' if ok else 'FAILED'} state_keys={keys} version={state.get('version')} "
        f"exit_code={code} leftover_children={left}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

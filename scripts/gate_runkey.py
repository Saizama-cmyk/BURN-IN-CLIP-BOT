"""G14: toggling Settings → App → Start with Windows adds and removes the HKCU Run key live."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import httpx
from _gatelib import CLIENT, BASE, H, fresh_home, out, start_dev, stop

KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"


def present() -> bool:
    return subprocess.run(["reg", "query", KEY, "/v", "ClipBot"], capture_output=True).returncode == 0


def main() -> int:
    before = present()
    proc = start_dev(fresh_home("runkey"))
    try:
        c = CLIENT
        c.put("/api/settings", headers=H, json={"settings": {"app": {"start_with_windows": True}}})
        added = present()
        c.put("/api/settings", headers=H, json={"settings": {"app": {"start_with_windows": False}}})
        removed = not present()
        ok = added and removed
        out(f"RUNKEY {'OK' if ok else 'FAILED'} added={added} removed={removed} "
            f"(was present before: {before})")
        return 0 if ok else 1
    finally:
        stop(proc)


if __name__ == "__main__":
    sys.exit(main())

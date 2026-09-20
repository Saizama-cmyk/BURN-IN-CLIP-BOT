"""G4: every config field appears in the generated Settings form.

Counts leaf fields in /api/settings/schema, renders the dashboard's Settings tab in headless
Microsoft Edge (JS executed) and counts the distinct [data-field] inputs in the DOM."""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import httpx
from _gatelib import CLIENT, BASE, fresh_home, out, start_dev, stop

EDGE = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")]
RENDER_BUDGET_MS = "20000"


def main() -> int:
    edge = next((p for p in EDGE if p.exists()), None)
    if not edge:
        out("FAILED: Microsoft Edge not found")
        return 1
    home = fresh_home("form")
    proc = start_dev(home)
    try:
        schema_count = CLIENT.get("/api/settings/schema", timeout=30).json()["leaf_count"]
        dom = subprocess.run([str(edge), "--headless=new", "--disable-gpu",
                              f"--user-data-dir={home / 'edge'}",
                              f"--virtual-time-budget={RENDER_BUDGET_MS}", "--dump-dom",
                              f"{BASE}/#settings"], capture_output=True, text=True,
                             encoding="utf-8", timeout=120).stdout
        markup = re.sub(r"<script.*?</script>", "", dom, flags=re.S)   # count real inputs only
        rendered = len(set(re.findall(r'data-field="([^"]+)"', markup)))
        ok = rendered == schema_count and schema_count > 0
        out(f"FORM FIELDS {'OK' if ok else 'FAILED'} schema={schema_count} rendered={rendered}")
        return 0 if ok else 1
    finally:
        stop(proc)


if __name__ == "__main__":
    sys.exit(main())

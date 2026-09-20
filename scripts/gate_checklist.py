"""G15/G16: on a fresh install the setup checklist lists what is missing, each item linked to
its Settings field, and discovery reports missing keys gracefully."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import httpx
from _gatelib import CLIENT, BASE, fresh_home, out, start_dev, stop


def main() -> int:
    home = fresh_home("checklist")
    proc = start_dev(home)
    try:
        setup = CLIENT.get("/api/setup", timeout=60).json()
        missing = [f"{i['id']}->{i['field']}" for i in setup["items"] if not i["ok"]]
        linked = all(i["field"] for i in setup["items"])
        msgs = CLIENT.get("/api/state", timeout=30).json()["discovery"]["messages"]
        log = (home / "logs" / "clipbot.log").read_text(encoding="utf-8")
        graceful = "no client ID/secret" in msgs.get("twitch", "") and "Traceback" not in log
        ok = not setup["ok"] and linked and "twitch->twitch.client_id" in missing and graceful
        out(f"CHECKLIST {'OK' if ok else 'FAILED'} missing={missing}")
        out(f"DISCOVERY MESSAGES {msgs} traceback_in_log={'Traceback' in log}")
        return 0 if ok else 1
    finally:
        stop(proc)


if __name__ == "__main__":
    sys.exit(main())

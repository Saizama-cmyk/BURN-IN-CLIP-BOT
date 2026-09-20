"""G3: settings round-trip through PUT/GET /api/settings; secrets masked out, masked ignored in."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import httpx
from _gatelib import CLIENT, BASE, H, fresh_home, out, start_dev, stop

MASK = "••••••••"


def main() -> int:
    proc = start_dev(fresh_home("settings"))
    try:
        c = CLIENT
        r = c.put("/api/settings", headers=H, json={"settings": {
            "detector": {"z_threshold": 4.75}, "twitch": {"client_secret": "gate-secret"}}})
        assert r.status_code == 200 and r.json()["ok"], r.text
        s = c.get("/api/settings").json()["settings"]
        changed = s["detector"]["z_threshold"]
        masked = s["twitch"]["client_secret"] == MASK
        # send everything back, including the masked placeholder, with one change
        s["brand"]["name"] = "GateBrand"
        assert c.put("/api/settings", headers=H, json={"settings": s}).json()["ok"]
        exp = c.get("/api/settings/export?include_secrets=true").json()
        ignored = exp["twitch"]["client_secret"] == "gate-secret" and exp["brand"]["name"] == "GateBrand"
        ok = changed == 4.75 and masked and ignored
        out(f"SETTINGS ROUNDTRIP {'OK' if ok else 'FAILED'} changed={changed} "
            f"secret_masked={masked} masked_ignored={ignored}")
        return 0 if ok else 1
    finally:
        stop(proc)


if __name__ == "__main__":
    sys.exit(main())

from clipbot import syslog
from clipbot.config import Settings

LOG = """2026-09-19 06:51:26,243 INFO    clipbot.detector: spike twitch:caedrel kind=chat z=5.90
2026-09-19 06:52:05,745 WARNING clipbot.transcribe: whisper CUDA unavailable
2026-09-19 06:53:00,000 INFO    httpx: HTTP Request: GET https://x?token=abc123 "200"
2026-09-19 06:54:00,000 ERROR   clipbot.vision: vision skipped: ReadTimeout
Traceback (most recent call last):
  File "x.py", line 1
2026-09-19 06:55:00,000 WARNING clipbot.vision: vision skipped: ReadTimeout
""".splitlines()


def test_parse_split_and_redact():
    entries = syslog.parse(LOG)
    assert len(entries) == 5 and entries[3]["detail"][0].startswith("Traceback")
    assert "abc123" not in entries[2]["msg"]
    out = syslog.split(entries, 50)
    assert [e["src"] for e in out["events"]] == ["clipbot.detector"]
    assert len(out["issues"]) == 3 and out["issue_groups"][0]["count"] == 2


def test_explain_failsafe_and_analytics():
    snap = {"status": "failsafe", "backlog": {"in_flight": 67, "capacity": 87, "pressure": 0.77,
                                               "pause_at": 0.8, "resume_at": 0.5}}
    e = syslog.explain(snap, Settings())
    assert "67 clips" in e["why"] and e["tips"]
    now = 1_000_000.0
    samples = [{"stage": "posted", "created_at": now - 100}, {"stage": "rejected", "created_at": now - 200,
                "verdict": {"category": "chat_only"}}, {"stage": "rejected", "created_at": now - 90000}]
    a = syslog.analytics(samples, {}, now)
    assert a["cut_24h"] == 2 and a["pass_rate"] == 50 and a["reject_reasons"] == [("chat_only", 1)]
    assert len(a["per_hour"]) == 24 and a["per_hour"][-1] == 2

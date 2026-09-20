import time

from clipbot.config import Settings
from clipbot.pets import GRAPH_HOURS, PetPlace, pet_state


def test_pet_state_shapes_snapshot():
    now = time.time()
    snap = {
        "status": "running", "manual_paused": False, "backlog": {"in_flight": 2},
        "stages": {"counts": {"scheduled": 3, "posted": 2, "rejected": 5}},
        "monitors": [
            {"key": "twitch_a", "display_name": "A", "viewers": 10, "chat": {"z": 1.0, "warm": True}},
            {"key": "twitch_b", "display_name": "B", "viewers": 5, "chat": {"z": 4.2, "warm": True}}],
        "samples": [{"id": "x", "stage": "posted", "created_at": now - 60,
                     "event": {"kind": "chat", "target": {"display_name": "B"}}, "verdict": {"score": 8}},
                    {"id": "y", "stage": "rejected", "created_at": now - 7200}],
        "scheduler": {"platforms": {"youtube": {"enabled": True, "posted_24h": 4},
                                    "tiktok": {"enabled": False, "posted_24h": 9}}},
        "ai": {"error": ""}, "chat": {"twitch": {"connected": True}},
    }
    s = pet_state(snap, Settings(), now)
    assert s["counts"]["clips"] == 5 and s["counts"]["rejected"] == 5 and s["counts"]["posted"] == 4
    assert s["streams"][0]["name"] == "B"                      # hottest chat first
    assert len(s["hours"]) == GRAPH_HOURS and s["hours"][-1] == 1 and s["passed_hours"][-1] == 1
    assert s["posted_by_platform"] == {"youtube": 4} and s["chat_connected"]
    assert s["pet"]["species"] == "blip" and s["trouble"] == []
    assert pet_state({"status": "failsafe"}, Settings())["trouble"]


def test_pet_place_roundtrip(tmp_path):
    pp = PetPlace(tmp_path / "pet.json")
    assert pp.load() == {}
    pp.save(120.6, 900)
    assert pp.load() == {"x": 120, "y": 900}
    (tmp_path / "pet.json").write_text("{broken", encoding="utf-8")
    assert pp.load() == {}


def test_pet_uses_complete_history_when_dashboard_samples_are_limited():
    history = {"hours": [0] * (GRAPH_HOURS - 1) + [130],
               "passed_hours": [0] * (GRAPH_HOURS - 1) + [110]}
    state = pet_state({"samples": []}, Settings(), history=history)
    assert state["hours"][-1] == 130 and state["passed_hours"][-1] == 110

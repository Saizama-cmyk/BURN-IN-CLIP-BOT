"""Titles that describe the clip get caught; titles that hook get through."""
from clipbot.config import Settings
from clipbot.copywriter import narrates


def test_describing_titles_are_caught_and_hooks_pass():
    patterns = Settings().copywriter.narration
    describing = ["Jynxzi laughs so hard he covers his mouth", "Tony Corleone rants about paper units",
                  "Xaryu says Dray-Nai and chat loses it", "Zackrawrr Reacts to Singapore Bus Fine Tweet",
                  "She Smiled While Chat Went Nuclear"]
    hooks = ['"I\'m never doing that again"', "He was one hit away...", "xQc was NOT ready for this"]
    for title in describing:
        assert narrates({"youtube": {"title": title}}, patterns), title
    for title in hooks:
        assert narrates({"youtube": {"title": title}}, patterns) == "", title


def test_a_broken_pattern_is_skipped_not_fatal():
    assert narrates({"hook": "chat went wild"}, ["(unclosed", r"\bchat went\b"]) == "chat went"

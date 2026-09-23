import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from clipbot import config as C


def test_defaults_roundtrip(isolated_home):
    s = C.load_settings()                       # creates the file on first run
    assert C.settings_path().exists() and C.settings_path().parent == isolated_home
    s2 = C.load_settings()
    assert s2 == s
    s3 = C.merge_incoming(s2, {"detector": {"z_threshold": 4.5}})
    C.save_settings(s3)
    assert C.load_settings().detector.z_threshold == 4.5


def test_user_data_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("CLIPBOT_HOME")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert C.home_dir() == tmp_path / "Ashvane"
    p = C.resolve_paths(C.Settings())
    assert p.db == tmp_path / "Ashvane" / "clipbot.db"
    assert p.clips == tmp_path / "Ashvane" / "clips"
    s = C.Settings(app=C.AppCfg(clips_dir=str(tmp_path / "elsewhere")))
    assert C.resolve_paths(s).clips == tmp_path / "elsewhere"


def test_old_data_folder_moves_and_paths_follow(monkeypatch, tmp_path):
    """An install from before the rename: its folder moves and stored clip paths still work."""
    import sqlite3
    monkeypatch.delenv("CLIPBOT_HOME")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    old, new = tmp_path / "ClipBot", tmp_path / "Ashvane"
    (old / "clips").mkdir(parents=True)
    clip = old / "clips" / "a.mp4"
    clip.write_bytes(b"x")
    con = sqlite3.connect(old / "clipbot.db")
    con.execute("create table candidates (id text, json text)")
    con.execute("insert into candidates values ('1', ?)", (json.dumps({"final": str(clip)}),))
    con.commit()
    con.close()
    (old / "facecams.json").write_text(json.dumps({"path": str(clip)}), encoding="utf-8")

    assert C.home_dir() == old                      # not moved yet: the old folder is still used
    assert C.migrate_data_dir() == new
    assert not old.exists() and C.home_dir() == new
    con = sqlite3.connect(new / "clipbot.db")
    stored = json.loads(con.execute("select json from candidates").fetchone()[0])["final"]
    con.close()
    assert stored == str(new / "clips" / "a.mp4") and Path(stored).exists()
    assert json.loads((new / "facecams.json").read_text(encoding="utf-8"))["path"] == stored
    assert C.migrate_data_dir() is None             # once only


def test_migration_leaves_a_current_folder_alone(monkeypatch, tmp_path):
    monkeypatch.delenv("CLIPBOT_HOME")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "ClipBot").mkdir()
    (tmp_path / "Ashvane").mkdir()
    assert C.migrate_data_dir() is None
    assert (tmp_path / "ClipBot").exists() and C.home_dir() == tmp_path / "Ashvane"


def test_every_field_has_label_and_help():
    for path, info in C._walk_fields(C.Settings):
        assert info.title, f"{'.'.join(path)} has no label"
        assert info.description, f"{'.'.join(path)} has no help text"


def test_secrets_masked_and_mask_ignored():
    s = C.merge_incoming(C.Settings(), {"twitch": {"client_secret": "abc"},
                                        "accounts": {"discord_webhook": "https://x"}})
    dumped = C.masked_dump(s)
    assert dumped["twitch"]["client_secret"] == C.MASK
    assert dumped["accounts"]["discord_webhook"] == C.MASK
    assert dumped["accounts"]["instagram_token"] == ""          # empty secrets stay empty
    back = C.merge_incoming(s, dumped)                          # UI sends the mask back
    assert back.twitch.client_secret == "abc"
    changed = C.merge_incoming(s, {"twitch": {"client_secret": "new"}})
    assert changed.twitch.client_secret == "new"


def test_map_fields_replace_wholesale():
    s = C.Settings()
    s2 = C.merge_incoming(s, {"detector": {"keywords": {"pog": 2.0}}})
    assert s2.detector.keywords == {"pog": 2.0}


def test_validation_errors():
    with pytest.raises(ValidationError):
        C.merge_incoming(C.Settings(), {"backlog": {"pause_at": 0.4, "resume_at": 0.6}})
    with pytest.raises(ValidationError):
        C.merge_incoming(C.Settings(), {"twitch": {"slots": -1}})
    with pytest.raises(ValidationError):
        C.merge_incoming(C.Settings(), {"clip": {"min_len_s": 60, "max_len_s": 30}})


def test_export_import_secrets():
    s = C.merge_incoming(C.Settings(), {"kick": {"client_secret": "k"}})
    plain = C.export_settings(s, include_secrets=False)
    assert plain["kick"]["client_secret"] == ""
    assert C.export_settings(s, include_secrets=True)["kick"]["client_secret"] == "k"
    imported = C.import_settings(s, json.loads(json.dumps(plain)))
    assert imported.kick.client_secret == "k"                 # blank in file keeps stored secret


def test_reset_section():
    s = C.merge_incoming(C.Settings(), {"edit": {"crf": 30}, "ai": {"min_score": 9}})
    r = C.reset_section(s, "edit")
    assert r.edit.crf == C.EditCfg().crf and r.ai.min_score == 9
    with pytest.raises(KeyError):
        C.reset_section(s, "nope")


def test_restart_and_secret_metadata():
    assert ("dashboard", "port") in C.RESTART_FIELDS
    assert ("accounts", "facebook_page_token") in C.SECRET_FIELDS
    schema = C.Settings.model_json_schema()
    assert schema["$defs"]["AICfg"]["properties"]["model"]["widget"] == "ollama_models"
    assert schema["$defs"]["AICfg"]["properties"]["system_prompt"]["widget"] == "textarea"


def test_corrupt_file_raises(isolated_home):
    C.settings_path().write_text("{nope", encoding="utf-8")
    with pytest.raises(C.ConfigError):
        C.load_settings()


def test_unknown_keys_ignored(isolated_home):
    C.settings_path().write_text(json.dumps({"brand": {"name": "X", "legacy": 1}, "gone": {}}),
                                 encoding="utf-8")
    assert C.load_settings().brand.name == "X"


def test_list_fields_split_commas_and_fix_logins():
    from clipbot.config import Settings, merge_incoming
    s = merge_incoming(Settings(), {"twitch": {
        "forced_streamers": ["kaicenat, ishowspeed", "Stable Ronaldo", ""],
        "forced_categories": ["just chatting, irl,"]}})
    assert s.twitch.forced_streamers == ["kaicenat", "ishowspeed", "stableronaldo"]
    assert s.twitch.forced_categories == ["just chatting", "irl"]


def test_old_default_prompt_is_upgraded_but_custom_text_is_kept():
    """A prompt nobody edited follows the app forward; an edited one is left alone."""
    old = next(iter(C.PROMPT_FIELDS[("ai", "system_prompt")][1]))
    assert len(old) == 64                      # a sha256 of a prompt we used to ship
    data = {"ai": {"system_prompt": "my own carefully tuned prompt"}}
    assert not C.upgrade_prompts(data)
    assert data["ai"]["system_prompt"] == "my own carefully tuned prompt"

    data = {"ai": {"system_prompt": C.DEFAULT_SYSTEM_PROMPT}}
    assert not C.upgrade_prompts(data)    # already current: nothing to do

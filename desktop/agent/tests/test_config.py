import json
import os
import stat

from kmr_desktop import config


def test_a_new_install_is_not_connected_and_garbage_is_ignored(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert config.load(tmp_path / "missing.json").connected is False
    (tmp_path / "bad.json").write_text("not json")
    assert config.load(tmp_path / "bad.json").connected is False
    (tmp_path / "odd.json").write_text(
        json.dumps({"language": "klingon", "extra_apps": [1, "", "x"], "token": 5})
    )
    cfg = config.load(tmp_path / "odd.json")
    assert cfg.language == "auto" and cfg.extra_apps == ["1", "x"]


def test_save_and_load_round_trip_with_private_permissions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "kmr" / "config.json"
    config.save(config.Config("https://kmr.example.com", "kmr_dt_abc", "hi", ["notes"]), path)
    loaded = config.load(path)
    assert loaded.connected and loaded.token == "kmr_dt_abc" and loaded.language == "hi"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_the_public_view_never_has_the_token() -> None:
    public = config.Config("https://x", "kmr_dt_secret").public()
    assert public["has_token"] is True and "kmr_dt_secret" not in json.dumps(public)

from __future__ import annotations

from datetime import datetime

from leetkit_manager.config_backup import backup_config, backup_path_for, restore_config


def test_backup_config_returns_none_when_file_missing(tmp_path):
    missing = tmp_path / "claude_desktop_config.json"
    assert backup_config(missing) is None


def test_backup_config_creates_timestamped_copy(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text('{"mcpServers": {}}', encoding="utf-8")

    at = datetime(2026, 8, 6, 14, 32, 0)
    backup = backup_config(cfg, at=at)

    assert backup is not None
    assert backup.name == "claude_desktop_config.json.bak-20260806-143200"
    assert backup.read_text(encoding="utf-8") == cfg.read_text(encoding="utf-8")


def test_backup_path_for_is_deterministic_given_same_timestamp(tmp_path):
    cfg = tmp_path / "x.json"
    at = datetime(2026, 1, 1, 0, 0, 0)
    assert backup_path_for(cfg, at=at) == backup_path_for(cfg, at=at)


def test_restore_config_writes_backup_content_back(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text('{"mcpServers": {"stocklens": {}}}', encoding="utf-8")
    backup = backup_config(cfg)

    cfg.write_text('{"mcpServers": {}}', encoding="utf-8")  # 이후 변경(장애 상황 흉내)
    assert restore_config(cfg, backup) is True
    assert "stocklens" in cfg.read_text(encoding="utf-8")


def test_restore_config_does_nothing_when_backup_missing(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text("original", encoding="utf-8")
    fake_backup = tmp_path / "does-not-exist.bak"

    assert restore_config(cfg, fake_backup) is False
    assert cfg.read_text(encoding="utf-8") == "original"

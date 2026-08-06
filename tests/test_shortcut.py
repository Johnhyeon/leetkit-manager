from __future__ import annotations

from unittest.mock import patch

from leetkit_manager import shortcut


def test_ensure_desktop_shortcut_once_only_calls_create_the_first_time(tmp_path):
    marker = tmp_path / "shortcut_created"
    with patch.object(shortcut, "_MARKER", marker), patch.object(
        shortcut, "create_desktop_shortcut"
    ) as mock_create:
        shortcut.ensure_desktop_shortcut_once()
        shortcut.ensure_desktop_shortcut_once()

    mock_create.assert_called_once()
    assert marker.exists()


def test_create_desktop_shortcut_returns_none_when_target_missing(tmp_path):
    with patch.object(shortcut, "_resolved_exe_path", return_value=str(tmp_path / "does-not-exist.exe")), \
         patch("sys.platform", "win32"):
        result = shortcut._create_windows_shortcut()
    assert result is None

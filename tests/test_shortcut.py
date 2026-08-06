from __future__ import annotations

from unittest.mock import patch

from leetkit_manager import shortcut


def test_has_shortcut_been_offered_reflects_marker(tmp_path):
    marker = tmp_path / "shortcut_created"
    with patch.object(shortcut, "_MARKER", marker):
        assert shortcut.has_shortcut_been_offered() is False
        shortcut.mark_shortcut_offered()
        assert shortcut.has_shortcut_been_offered() is True


def test_mark_shortcut_offered_is_idempotent(tmp_path):
    marker = tmp_path / "nested" / "shortcut_created"
    with patch.object(shortcut, "_MARKER", marker):
        shortcut.mark_shortcut_offered()
        shortcut.mark_shortcut_offered()
    assert marker.exists()


def test_resolved_exe_path_uses_sys_executable_when_frozen():
    """실사용 중 발견된 문제 재현: 단일 exe로 복사해 실행하면 uv tool bin 디렉터리엔
    "leetkit-manager"가 없어서 resolve_lens_command가 bare 문자열을 그대로 반환했고,
    그건 실제 파일이 아니라서 바로가기가 아무 에러도 없이 조용히 안 만들어졌다."""
    with patch.object(shortcut.package_service, "is_frozen_exe", return_value=True), \
         patch.object(shortcut.sys, "executable", "C:/somewhere/LeetKitManager.exe"), \
         patch.object(shortcut.package_service, "resolve_lens_command") as mock_resolve:
        assert shortcut._resolved_exe_path() == "C:/somewhere/LeetKitManager.exe"
    mock_resolve.assert_not_called()


def test_resolved_exe_path_uses_uv_tool_resolution_when_not_frozen():
    with patch.object(shortcut.package_service, "is_frozen_exe", return_value=False), \
         patch.object(shortcut.package_service, "resolve_lens_command", return_value="/home/x/.local/bin/leetkit-manager") as mock_resolve:
        assert shortcut._resolved_exe_path() == "/home/x/.local/bin/leetkit-manager"
    mock_resolve.assert_called_once_with("leetkit-manager")


def test_create_windows_shortcut_returns_none_when_target_missing(tmp_path):
    with patch.object(shortcut, "_resolved_exe_path", return_value=str(tmp_path / "does-not-exist.exe")), \
         patch("sys.platform", "win32"):
        result = shortcut._create_windows_shortcut(tmp_path)
    assert result is None


def test_create_windows_shortcut_returns_none_when_target_dir_missing(tmp_path):
    missing_dir = tmp_path / "no-such-folder"
    with patch("sys.platform", "win32"):
        result = shortcut._create_windows_shortcut(missing_dir)
    assert result is None


def test_create_shortcut_at_dispatches_by_platform(tmp_path):
    with patch("sys.platform", "win32"), \
         patch.object(shortcut, "_create_windows_shortcut", return_value=tmp_path / "LeetKit Manager.lnk") as mock_win, \
         patch.object(shortcut, "_create_macos_alias") as mock_mac:
        result = shortcut.create_shortcut_at(tmp_path)
    mock_win.assert_called_once_with(tmp_path)
    mock_mac.assert_not_called()
    assert result == tmp_path / "LeetKit Manager.lnk"


def test_create_desktop_shortcut_uses_home_desktop(tmp_path):
    with patch.object(shortcut, "create_shortcut_at", return_value=None) as mock_create:
        shortcut.create_desktop_shortcut()
    mock_create.assert_called_once_with(shortcut.Path.home() / "Desktop")

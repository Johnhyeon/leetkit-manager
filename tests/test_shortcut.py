from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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
         patch.object(shortcut, "_create_macos_app_bundle") as mock_mac:
        result = shortcut.create_shortcut_at(tmp_path)
    mock_win.assert_called_once_with(tmp_path)
    mock_mac.assert_not_called()
    assert result == tmp_path / "LeetKit Manager.lnk"


def test_create_desktop_shortcut_uses_home_desktop(tmp_path):
    with patch.object(shortcut, "create_shortcut_at", return_value=None) as mock_create:
        shortcut.create_desktop_shortcut()
    mock_create.assert_called_once_with(shortcut.Path.home() / "Desktop")


class TestMacosAppBundle:
    """맥에서 예전엔 심볼릭 링크였는데 세 가지가 한꺼번에 문제였다 — 아이콘이 안 붙고,
    도크 이름이 "Python"으로 뜨고, 더블클릭하면 터미널이 같이 떴다. .app 번들이 셋을
    한 번에 해결한다. (실제 Finder 동작은 맥에서만 확인 가능 — 여기서는 번들 구조가
    Apple이 요구하는 모양인지까지 검사한다.)"""

    def _make(self, tmp_path, exe_name="leetkit-manager"):
        fake_exe = tmp_path / exe_name
        fake_exe.write_text("#!/bin/sh\n", encoding="utf-8")
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        with patch.object(shortcut, "_resolved_exe_path", return_value=str(fake_exe)):
            return shortcut._create_macos_app_bundle(desktop), desktop, fake_exe

    def test_creates_the_bundle_layout_apple_requires(self, tmp_path):
        app, desktop, _ = self._make(tmp_path)
        assert app == desktop / "LeetKit Manager.app"
        assert (app / "Contents" / "Info.plist").is_file()
        assert (app / "Contents" / "MacOS" / "LeetKitManager").is_file()

    def test_display_name_comes_from_the_bundle_not_python(self, tmp_path):
        """이게 없으면 도크·메뉴막대에 "Python"이 뜬다 — 실행 주체가 파이썬이라서."""
        app, _, _ = self._make(tmp_path)
        plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
        assert "<key>CFBundleName</key><string>LeetKit Manager</string>" in plist
        assert "<key>CFBundleDisplayName</key><string>LeetKit Manager</string>" in plist
        # CFBundleExecutable이 실제 파일명과 다르면 Finder가 번들을 못 연다
        assert "<key>CFBundleExecutable</key><string>LeetKitManager</string>" in plist
        assert (app / "Contents" / "MacOS" / "LeetKitManager").exists()

    def test_bundle_carries_the_icon(self, tmp_path):
        """링크에는 아이콘을 지정할 방법이 아예 없었다 — 번들은 Resources로 붙인다."""
        app, _, _ = self._make(tmp_path)
        assert (app / "Contents" / "Resources" / "icon.icns").is_file()
        plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
        assert "<key>CFBundleIconFile</key><string>icon</string>" in plist

    def test_shipped_icns_is_a_real_icns(self):
        """번들이 참조하는 파일이 깨져 있으면 아이콘이 조용히 기본값으로 나온다."""
        import struct

        icns = Path(shortcut.__file__).parent / "ui" / "icon.icns"
        raw = icns.read_bytes()
        assert raw[:4] == b"icns"
        assert struct.unpack(">I", raw[4:8])[0] == len(raw), "헤더의 길이와 실제 크기가 다르다"

    def test_launcher_is_marked_executable(self, tmp_path):
        """실행 권한이 없으면 Finder가 번들을 아예 안 연다. 실행 비트 자체는 Windows가
        무시하므로(개발은 Windows에서 한다) 우리가 제대로 요청했는지를 본다."""
        real_chmod = Path.chmod
        seen = {}

        def spy(self, mode, **kwargs):
            if self.name == "LeetKitManager":
                seen["mode"] = mode
            return real_chmod(self, mode, **kwargs)

        with patch.object(Path, "chmod", spy):
            self._make(tmp_path)
        assert seen.get("mode") == 0o755

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows는 실행 비트를 안 쓴다")
    def test_launcher_really_is_executable_on_posix(self, tmp_path):
        app, _, _ = self._make(tmp_path)
        launcher = app / "Contents" / "MacOS" / "LeetKitManager"
        assert launcher.stat().st_mode & 0o111, "실행 비트가 없다"

    def test_launcher_quotes_the_path(self, tmp_path):
        """홈 폴더 이름에 공백이 있는 사람이 실제로 있다."""
        spaced = tmp_path / "My Apps"
        spaced.mkdir()
        fake_exe = spaced / "leetkit-manager"
        fake_exe.write_text("#!/bin/sh\n", encoding="utf-8")
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        with patch.object(shortcut, "_resolved_exe_path", return_value=str(fake_exe)):
            app = shortcut._create_macos_app_bundle(desktop)
        script = (app / "Contents" / "MacOS" / "LeetKitManager").read_text(encoding="utf-8")
        assert f'exec "{fake_exe}" gui' in script

    def test_replaces_the_old_symlink(self, tmp_path):
        """예전 버전이 만든 링크가 남아 있으면 아이콘 없는 항목이 옆에 계속 보인다."""
        fake_exe = tmp_path / "leetkit-manager"
        fake_exe.write_text("#!/bin/sh\n", encoding="utf-8")
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        legacy = desktop / "LeetKit Manager"
        try:
            legacy.symlink_to(fake_exe)
        except (OSError, NotImplementedError):
            pytest.skip("이 환경에서는 심볼릭 링크를 만들 수 없음(Windows 권한)")
        with patch.object(shortcut, "_resolved_exe_path", return_value=str(fake_exe)):
            shortcut._create_macos_app_bundle(desktop)
        assert not legacy.exists() and not legacy.is_symlink()

    def test_rerunning_repairs_an_existing_bundle(self, tmp_path):
        """예전 버전이 만든(혹은 손상된) 번들 위에 다시 만들어도 정상이어야 한다 —
        업데이트로 고쳐지는 게 사용자가 기대하는 동작이다."""
        app, desktop, fake_exe = self._make(tmp_path)
        (app / "Contents" / "Info.plist").write_text("깨짐", encoding="utf-8")
        with patch.object(shortcut, "_resolved_exe_path", return_value=str(fake_exe)):
            again = shortcut._create_macos_app_bundle(desktop)
        assert again == app
        plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
        assert "CFBundleName" in plist


class TestMacosAppIdentity:
    """도크·메뉴막대 이름 — .app 번들만으로는 부족할 수 있다. 번들 안의 스크립트가
    번들 밖 파이썬을 exec하면 macOS가 그 파이썬 위치로 앱을 판별해 다시 "Python"이
    뜬다. 프로세스 안에서 직접 박아 그 경우까지 막는다."""

    def test_does_nothing_off_darwin(self):
        """윈도우에서 이 코드가 돌면 안 된다(PyObjC가 없다)."""
        from leetkit_manager.ui import app

        with patch.object(app.sys, "platform", "win32"):
            app._apply_macos_app_identity()  # 예외가 나면 실패

    def test_survives_missing_pyobjc(self):
        """PyObjC가 없거나 API가 바뀌어도 창은 떠야 한다 — 이름이 좀 이상한 것보다
        앱이 안 뜨는 게 훨씬 나쁘다."""
        from leetkit_manager.ui import app

        with patch.object(app.sys, "platform", "darwin"):
            app._apply_macos_app_identity()  # import 실패를 삼키는지

    def test_is_called_before_the_window_starts(self):
        """webview.start() 뒤에 부르면 이미 NSApplication이 이름을 읽은 뒤다."""
        import inspect

        from leetkit_manager.ui import app

        source = inspect.getsource(app.run)
        # rindex: 주석에도 "webview.start()"가 나온다 — 실제 호출은 맨 뒤 것이다.
        assert source.index("_apply_macos_app_identity()") < source.rindex("webview.start(")

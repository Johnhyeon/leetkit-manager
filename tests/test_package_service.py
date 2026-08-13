from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from leetkit_manager import package_service
from leetkit_manager.package_service import resolve_lens_command
from leetkit_manager.process_runner import ProcessResult


def test_resolve_lens_command_falls_back_to_bare_name_when_not_in_uv_bin_dirs(tmp_path):
    with patch("leetkit_manager.package_service._uv_tool_bin_dirs", return_value=[tmp_path]):
        assert resolve_lens_command("stocklens-doctor") == "stocklens-doctor"


def test_resolve_lens_command_prefers_uv_tool_bin_dir_over_bare_name(tmp_path):
    """실사용 중 발견된 문제 재현: uv tool bin dir에 있는 최신 실행 파일을 PATH
    검색보다 먼저 찾아야 한다(옛 pip 설치 잔재가 PATH에서 먼저 잡히는 경우 대비)."""
    fake_exe = tmp_path / "stocklens-doctor.exe"
    fake_exe.write_text("", encoding="utf-8")

    with patch("leetkit_manager.package_service._uv_tool_bin_dirs", return_value=[tmp_path]):
        resolved = resolve_lens_command("stocklens-doctor")

    assert resolved == str(fake_exe)


class TestEnsureUvAvailable:
    def test_is_uv_available_true_when_on_path(self):
        with patch("shutil.which", return_value="C:/fake/uv.exe"):
            assert package_service.is_uv_available() is True

    def test_is_uv_available_true_when_in_tool_bin_dir(self, tmp_path):
        (tmp_path / "uv.exe").write_text("", encoding="utf-8")
        with patch("shutil.which", return_value=None), \
             patch.object(package_service, "_uv_tool_bin_dirs", return_value=[tmp_path]), \
             patch.object(package_service.Path, "home", return_value=tmp_path):
            assert package_service.is_uv_available() is True

    def test_is_uv_available_false_when_nowhere(self, tmp_path):
        with patch("shutil.which", return_value=None), \
             patch.object(package_service, "_uv_tool_bin_dirs", return_value=[]), \
             patch.object(package_service.Path, "home", return_value=tmp_path):
            assert package_service.is_uv_available() is False

    def test_ensure_uv_available_skips_install_when_already_present(self):
        with patch.object(package_service, "is_uv_available", return_value=True), \
             patch.object(package_service, "run_cli") as mock_run:
            result = package_service.ensure_uv_available()
        mock_run.assert_not_called()
        assert result.ok is True

    def test_ensure_uv_available_runs_installer_when_missing(self):
        with patch.object(package_service, "is_uv_available", return_value=False), \
             patch("sys.platform", "win32"), \
             patch.object(
                 package_service, "run_cli",
                 return_value=ProcessResult(cmd=["x"], exit_code=0, stdout="", stderr="", timed_out=False, duration_s=0.1),
             ) as mock_run:
            result = package_service.ensure_uv_available()
        assert result.ok is True
        cmd = mock_run.call_args[0][0]
        assert "irm https://astral.sh/uv/install.ps1 | iex" in " ".join(cmd)

    def test_install_version_installs_uv_first_when_missing(self):
        with patch.object(package_service, "is_uv_available", return_value=False), \
             patch.object(
                 package_service, "ensure_uv_available",
                 return_value=ProcessResult(cmd=["x"], exit_code=0, stdout="", stderr="", timed_out=False, duration_s=0.1),
             ) as mock_ensure, \
             patch.object(package_service, "run_cli_streaming") as mock_run:
            package_service.install_version("stocklens-mcp", "1.0.0")
        mock_ensure.assert_called_once()
        mock_run.assert_called_once()

    def test_install_version_surfaces_uv_install_failure_without_attempting_tool_install(self):
        failure = ProcessResult(cmd=["x"], exit_code=1, stdout="", stderr="boom", timed_out=False, duration_s=0.1)
        with patch.object(package_service, "is_uv_available", return_value=False), \
             patch.object(package_service, "ensure_uv_available", return_value=failure), \
             patch.object(package_service, "run_cli") as mock_run:
            result = package_service.install_version("stocklens-mcp", "1.0.0")
        mock_run.assert_not_called()
        assert result is failure

    def test_install_version_skips_uv_bootstrap_when_already_available(self):
        with patch.object(package_service, "is_uv_available", return_value=True), \
             patch.object(package_service, "ensure_uv_available") as mock_ensure, \
             patch.object(package_service, "run_cli_streaming") as mock_run:
            package_service.install_version("stocklens-mcp", "1.0.0")
        mock_ensure.assert_not_called()
        mock_run.assert_called_once()

    def test_uninstall_version_runs_uv_tool_uninstall(self):
        with patch.object(package_service, "is_uv_available", return_value=True), \
             patch.object(package_service, "resolve_uv_command", return_value="uv"), \
             patch.object(
                 package_service, "run_cli",
                 return_value=ProcessResult(cmd=["x"], exit_code=0, stdout="", stderr="", timed_out=False, duration_s=0.1),
             ) as mock_run:
            result = package_service.uninstall_version("stocklens-mcp")
        assert result.ok is True
        mock_run.assert_called_once_with(["uv", "tool", "uninstall", "stocklens-mcp"], timeout=package_service._INSTALL_TIMEOUT)

    def test_uninstall_version_treats_missing_uv_as_already_uninstalled(self):
        """uv 자체가 없으면 uv로 설치된 것도 있을 수 없다 — 지우려던 걸 못 찾았다고
        실패로 보고할 이유가 없다(설치와 달리 uv 부트스트랩을 시도하지 않는다)."""
        with patch.object(package_service, "is_uv_available", return_value=False), \
             patch.object(package_service, "run_cli") as mock_run:
            result = package_service.uninstall_version("stocklens-mcp")
        assert result.ok is True
        mock_run.assert_not_called()


class TestInstallProgress:
    """설치는 수십 초 걸린다 — 그동안 화면이 멈춘 것처럼 보이면 사용자가 창을 닫는다."""

    def test_uv_lines_are_translated_to_customer_language(self):
        assert "내려받는 중" in package_service._humanize_uv_line("Downloading pandas (9.5MiB)")
        assert "내려받기 완료" in package_service._humanize_uv_line("Downloaded pandas")
        assert package_service._humanize_uv_line("Resolved 65 packages in 717ms")
        assert package_service._humanize_uv_line("Prepared 11 packages in 7.55s")
        assert package_service._humanize_uv_line("Installed 65 packages in 6.15s")

    def test_unrecognized_lines_are_ignored_so_internal_logs_never_leak(self):
        """알아볼 수 없는 줄을 그대로 노출하면 고객에게 영문 내부 로그가 보인다."""
        assert package_service._humanize_uv_line(" + some-internal-package==1.2.3") is None
        assert package_service._humanize_uv_line("warning: PATH ...") is None
        assert package_service._humanize_uv_line("") is None

    def test_blank_progress_does_not_wipe_previous_message(self):
        package_service._set_install_progress("내려받는 중…")
        package_service._set_install_progress("   ")
        assert package_service.current_install_progress() == "내려받는 중…"
        package_service._set_install_progress(None)  # 정리

    def test_install_version_clears_progress_when_done(self):
        with patch.object(package_service, "is_uv_available", return_value=True), \
             patch.object(package_service, "run_cli_streaming", return_value=_ok_result()):
            package_service.install_version("stocklens-mcp", "1.0.0")
        assert package_service.current_install_progress() is None


def _ok_result() -> ProcessResult:
    return ProcessResult(cmd=["x"], exit_code=0, stdout="", stderr="", timed_out=False, duration_s=0.1)


class TestResolveUvCommand:
    """실사용 중 재현해서 확인한 문제: astral 설치 스크립트는 영구 PATH(레지스트리)만
    갱신하므로, 방금 uv를 깐 직후에도 *실행 중인* 이 프로세스의 PATH에는 없다.
    bare "uv"로 부르면 not_found로 실패하는데 is_uv_available()은 bin dir 스캔으로
    True를 돌려줘서 부트스트랩까지 건너뛰었다 — 재부팅 전까지 설치가 계속 실패했다."""

    def test_returns_absolute_path_from_install_dir_when_not_on_path(self, tmp_path):
        bin_dir = tmp_path / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "uv.exe").write_text("", encoding="utf-8")
        with patch("shutil.which", return_value=None), \
             patch.object(package_service, "_uv_tool_bin_dirs", return_value=[]), \
             patch.object(package_service.Path, "home", return_value=tmp_path):
            assert package_service.resolve_uv_command() == str(bin_dir / "uv.exe")

    def test_prefers_path_hit_when_absolute(self):
        with patch("shutil.which", return_value="C:/real/uv.exe"):
            assert package_service.resolve_uv_command() == "C:/real/uv.exe"

    def test_ignores_relative_which_result(self, tmp_path):
        """Windows shutil.which()는 현재 작업 디렉터리도 뒤지고 상대경로를 돌려준다 —
        그건 PATH에서 제대로 찾은 게 아니므로 신뢰하지 않는다."""
        with patch("shutil.which", return_value=".\\uv.EXE"), \
             patch.object(package_service, "_uv_tool_bin_dirs", return_value=[]), \
             patch.object(package_service.Path, "home", return_value=tmp_path):
            assert package_service.resolve_uv_command() == "uv"

    def test_falls_back_to_bare_name_when_nowhere(self, tmp_path):
        with patch("shutil.which", return_value=None), \
             patch.object(package_service, "_uv_tool_bin_dirs", return_value=[]), \
             patch.object(package_service.Path, "home", return_value=tmp_path):
            assert package_service.resolve_uv_command() == "uv"

    def test_install_version_invokes_resolved_uv_not_bare_name(self, tmp_path):
        with patch.object(package_service, "is_uv_available", return_value=True), \
             patch.object(package_service, "resolve_uv_command", return_value="C:/somewhere/uv.exe"), \
             patch.object(package_service, "run_cli_streaming") as mock_run:
            package_service.install_version("stocklens-mcp", "1.0.0")
        assert mock_run.call_args[0][0][0] == "C:/somewhere/uv.exe"

    def test_uninstall_version_invokes_resolved_uv_not_bare_name(self):
        with patch.object(package_service, "is_uv_available", return_value=True), \
             patch.object(package_service, "resolve_uv_command", return_value="C:/somewhere/uv.exe"), \
             patch.object(package_service, "run_cli") as mock_run:
            package_service.uninstall_version("stocklens-mcp")
        assert mock_run.call_args[0][0][0] == "C:/somewhere/uv.exe"


class TestLegacyPipShadow:
    """실사용 중 발견된 문제 재현: 옛 pip 설치 잔재가 PATH에서 uv가 새로 설치한
    버전보다 먼저 잡히면, uv tool uninstall/install만으로는 "호환되지 않는 버전"이
    안 풀린다 — 그 그림자 실행 파일을 찾아서 pip로 따로 지워야 한다."""

    def test_find_legacy_pip_shadow_returns_none_when_command_not_on_path(self):
        with patch("shutil.which", return_value=None):
            assert package_service.find_legacy_pip_shadow(["stocklens-doctor"]) is None

    def test_find_legacy_pip_shadow_returns_none_when_inside_uv_bin_dir(self, tmp_path):
        uv_bin = tmp_path / "uv-bin"
        uv_bin.mkdir()
        exe = uv_bin / "stocklens-doctor.exe"
        exe.write_text("", encoding="utf-8")
        with patch("shutil.which", return_value=str(exe)), \
             patch.object(package_service, "_uv_tool_bin_dirs", return_value=[uv_bin]):
            assert package_service.find_legacy_pip_shadow(["stocklens-doctor"]) is None

    def test_find_legacy_pip_shadow_returns_path_when_outside_uv_bin_dir(self, tmp_path):
        pip_scripts = tmp_path / "Python311" / "Scripts"
        pip_scripts.mkdir(parents=True)
        exe = pip_scripts / "stocklens-doctor.exe"
        exe.write_text("", encoding="utf-8")
        with patch("shutil.which", return_value=str(exe)), \
             patch.object(package_service, "_uv_tool_bin_dirs", return_value=[tmp_path / "other-uv-bin"]):
            result = package_service.find_legacy_pip_shadow(["stocklens-doctor"])
        assert result == exe.resolve()

    def test_find_legacy_pip_shadow_checks_multiple_commands_in_order(self, tmp_path):
        pip_scripts = tmp_path / "Scripts"
        pip_scripts.mkdir()
        exe = pip_scripts / "stocklens-setup.exe"
        exe.write_text("", encoding="utf-8")

        def fake_which(name):
            return str(exe) if name == "stocklens-setup" else None

        with patch("shutil.which", side_effect=fake_which), \
             patch.object(package_service, "_uv_tool_bin_dirs", return_value=[]):
            result = package_service.find_legacy_pip_shadow(["stocklens-doctor", "stocklens-setup", "stocklens-activate"])
        assert result == exe.resolve()

    def test_infer_python_for_script_finds_sibling_python(self, tmp_path):
        (tmp_path / "python.exe").write_text("", encoding="utf-8")
        script = tmp_path / "stocklens-doctor.exe"
        script.write_text("", encoding="utf-8")
        assert package_service._infer_python_for_script(script) == tmp_path / "python.exe"

    def test_infer_python_for_script_finds_parent_level_python(self, tmp_path):
        """표준 Windows 설치 레이아웃 — python.exe는 Scripts/의 부모 폴더에 있다."""
        (tmp_path / "python.exe").write_text("", encoding="utf-8")
        scripts_dir = tmp_path / "Scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "stocklens-doctor.exe"
        script.write_text("", encoding="utf-8")
        assert package_service._infer_python_for_script(script) == tmp_path / "python.exe"

    def test_infer_python_for_script_returns_none_when_unrecognized_layout(self, tmp_path):
        script = tmp_path / "stocklens-doctor.exe"
        script.write_text("", encoding="utf-8")
        assert package_service._infer_python_for_script(script) is None

    def test_uninstall_legacy_pip_shadow_runs_pip_uninstall_with_inferred_python(self, tmp_path):
        python_exe = tmp_path / "python.exe"
        python_exe.write_text("", encoding="utf-8")
        script = tmp_path / "stocklens-doctor.exe"
        script.write_text("", encoding="utf-8")

        with patch.object(
            package_service, "run_cli",
            return_value=ProcessResult(cmd=["x"], exit_code=0, stdout="", stderr="", timed_out=False, duration_s=0.1),
        ) as mock_run:
            result = package_service.uninstall_legacy_pip_shadow("stocklens-mcp", script)

        assert result.ok is True
        mock_run.assert_called_once_with(
            [str(python_exe), "-m", "pip", "uninstall", "stocklens-mcp", "-y"],
            timeout=package_service._INSTALL_TIMEOUT,
        )

    def test_uninstall_legacy_pip_shadow_fails_safely_when_python_not_found(self, tmp_path):
        script = tmp_path / "stocklens-doctor.exe"
        script.write_text("", encoding="utf-8")
        with patch.object(package_service, "run_cli") as mock_run:
            result = package_service.uninstall_legacy_pip_shadow("stocklens-mcp", script)
        assert result.ok is False
        assert "python.exe" in result.stderr
        mock_run.assert_not_called()


class TestIsClaudeDesktopRunning:
    """MCP 등록은 설정 파일만 고치고 Claude Desktop은 그 파일을 켤 때 읽는다 —
    이미 떠 있으면 재시작 안내를 해야 고객이 "설치가 안 됐다"고 오해하지 않는다.

    판별은 반드시 *경로*로 한다. 실기기에서 확인한 바로 `claude.exe`는 두 종류가
    동시에 뜬다(Claude Desktop / Claude Code CLI) — 이름만 보면 CLI만 쓰는 사용자에게
    엉뚱한 재시작 안내가 나가고, 종료 기능이 CLI 작업 세션까지 죽인다."""

    _MSIX = r"C:\Program Files\WindowsApps\Claude_1.25927.0.0_x64__pzs8sxrjxfjjc\app\claude.exe"
    _CLASSIC = r"C:\Users\me\AppData\Local\AnthropicClaude\app-1.2.3\claude.exe"
    _CLI = r"C:\Users\me\.local\bin\claude.exe"

    def _fake_proc(self, name, exe, pid=4321):
        proc = type("P", (), {})()
        proc.info = {"name": name, "exe": exe}
        proc.pid = pid
        return proc

    def test_true_for_msix_install(self):
        with patch("psutil.process_iter", return_value=[self._fake_proc("claude.exe", self._MSIX)]):
            assert package_service.is_claude_desktop_running() is True

    def test_true_for_classic_installer(self):
        with patch("psutil.process_iter", return_value=[self._fake_proc("claude.exe", self._CLASSIC)]):
            assert package_service.is_claude_desktop_running() is True

    def test_case_insensitive(self):
        with patch("psutil.process_iter", return_value=[self._fake_proc("Claude.EXE", self._MSIX.upper())]):
            assert package_service.is_claude_desktop_running() is True

    def test_claude_code_cli_is_not_claude_desktop(self):
        """이 테스트가 이 클래스의 존재 이유 — CLI를 데스크탑으로 오인하면
        재시작 기능이 사용자의 CLI 작업을 죽인다."""
        with patch("psutil.process_iter", return_value=[self._fake_proc("claude.exe", self._CLI)]):
            assert package_service.is_claude_desktop_running() is False
            assert package_service.claude_desktop_processes() == []

    def test_mixed_environment_selects_only_desktop(self):
        procs = [
            self._fake_proc("claude.exe", self._CLI),
            self._fake_proc("claude.exe", self._MSIX),
            self._fake_proc("chrome.exe", r"C:\chrome.exe"),
        ]
        with patch("psutil.process_iter", return_value=procs):
            selected = package_service.claude_desktop_processes()
        assert len(selected) == 1
        assert selected[0].info["exe"] == self._MSIX

    def test_false_when_exe_path_unreadable(self):
        """경로를 못 읽으면 판단을 보류한다 — 모르는 프로세스를 종료 대상으로
        삼는 것보다 안내를 생략하는 쪽이 안전하다."""
        with patch("psutil.process_iter", return_value=[self._fake_proc("claude.exe", None)]):
            assert package_service.is_claude_desktop_running() is False

    def test_false_when_absent(self):
        procs = [self._fake_proc("chrome.exe", r"C:\chrome.exe")]
        with patch("psutil.process_iter", return_value=procs):
            assert package_service.is_claude_desktop_running() is False

    def test_false_when_psutil_raises(self):
        with patch("psutil.process_iter", side_effect=Exception("no access")):
            assert package_service.is_claude_desktop_running() is False

    @pytest.mark.parametrize(
        "path",
        [
            # 버전이 다른 사용자
            r"C:\Program Files\WindowsApps\Claude_2.0.1.0_x64__pzs8sxrjxfjjc\app\claude.exe",
            # 퍼블리셔 해시가 다른 경우
            r"C:\Program Files\WindowsApps\Claude_1.0.0.0_x64__abcdefghijklm\app\claude.exe",
            # 스토어 앱을 다른 드라이브에 설치한 사용자
            r"D:\WindowsApps\Claude_1.25927.0.0_x64__pzs8sxrjxfjjc\app\claude.exe",
            # arm64 기기
            r"C:\Program Files\WindowsApps\Claude_1.2.3.0_arm64__pzs8sxrjxfjjc\app\claude.exe",
            # 고전 인스톨러 — 사용자명·버전 제각각
            r"C:\Users\영희\AppData\Local\AnthropicClaude\app-1.2.3\claude.exe",
            # 슬래시 표기
            "C:/Program Files/WindowsApps/Claude_1.25927.0.0_x64__pzs8sxrjxfjjc/app/claude.exe",
        ],
    )
    def test_detects_desktop_across_user_environments(self, path):
        """설치 경로는 버전·퍼블리셔 해시·드라이브·아키텍처가 사용자마다 다르다 —
        전체 경로가 아니라 변하지 않는 조각으로만 판별해야 한다."""
        assert package_service._is_claude_desktop_exe(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            r"C:\Users\whdqj\.local\bin\claude.exe",
            r"C:\Users\영희\.local\bin\claude.exe",
            r"C:\Users\bob\AppData\Roaming\npm\claude.exe",
            r"C:\proj\node_modules\.bin\claude.exe",
            r"C:\Users\bob\.bun\bin\claude.exe",
        ],
    )
    def test_never_treats_cli_locations_as_desktop(self, path):
        """오탐의 대가가 가장 큰 쪽 — 종료 기능이 사용자의 CLI 작업을 죽인다."""
        assert package_service._is_claude_desktop_exe(path, pid=1234) is False

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/Applications/Claude.app/Contents/MacOS/Claude", True),
            ("/Users/me/Applications/Claude.app/Contents/MacOS/Claude", True),
            ("/Users/me/.local/bin/claude", False),   # Claude Code CLI
            ("/usr/local/bin/claude", False),         # Homebrew·수동 설치 CLI
            ("/opt/homebrew/bin/claude", False),
        ],
    )
    def test_macos_paths(self, path, expected):
        """경로 정규화를 한쪽 OS 표기만 가정하면 다른 OS에서 조용히 안 잡힌다 —
        실제로 macOS 경로가 전부 미검출되는 걸 확인하고 고쳤다."""
        assert package_service._is_claude_desktop_exe(path, pid=None) is expected

    def test_normalize_exe_path_unifies_separators_and_case(self):
        assert package_service._normalize_exe_path(r"C:\Users\Me\App.EXE") == "c:/users/me/app.exe"
        assert package_service._normalize_exe_path("/Users/Me/App") == "/users/me/app"

    def test_unknown_location_falls_back_to_visible_window_check(self):
        """앞으로 설치 위치가 바뀌어도 GUI인지로 구분할 수 있어야 한다."""
        unknown = r"C:\Program Files\Claude\claude.exe"
        with patch.object(package_service, "_process_has_visible_window", return_value=True):
            assert package_service._is_claude_desktop_exe(unknown, pid=42) is True
        with patch.object(package_service, "_process_has_visible_window", return_value=False):
            assert package_service._is_claude_desktop_exe(unknown, pid=42) is False

    def test_aumid_derived_from_msix_path(self):
        assert package_service._claude_desktop_aumid(self._MSIX) == "Claude_pzs8sxrjxfjjc!Claude"

    def test_aumid_none_for_classic_installer(self):
        assert package_service._claude_desktop_aumid(self._CLASSIC) is None


class TestRestartClaudeDesktop:
    def test_reports_error_when_relaunch_fails(self):
        with patch.object(package_service, "claude_desktop_processes", return_value=[]), \
             patch.object(package_service, "launch_claude_desktop", return_value=False):
            result = package_service.restart_claude_desktop()
        assert result["ok"] is False
        assert "다시 실행" in result["error"]

    def test_ok_when_quit_and_launch_succeed(self):
        with patch.object(package_service, "claude_desktop_processes", return_value=[]), \
             patch.object(package_service, "launch_claude_desktop", return_value=True) as mock_launch:
            result = package_service.restart_claude_desktop()
        assert result["ok"] is True
        mock_launch.assert_called_once()

    def test_reports_error_when_quit_fails(self):
        fake = type("P", (), {})()
        fake.info = {"name": "claude.exe", "exe": TestIsClaudeDesktopRunning._MSIX}
        with patch.object(package_service, "claude_desktop_processes", return_value=[fake]), \
             patch.object(package_service, "quit_claude_desktop", return_value=False), \
             patch.object(package_service, "launch_claude_desktop") as mock_launch:
            result = package_service.restart_claude_desktop()
        assert result["ok"] is False
        assert "종료" in result["error"]
        mock_launch.assert_not_called()  # 못 껐으면 켜지도 않는다(중복 실행 방지)


class TestIsCodexInstalled:
    def test_true_when_on_path(self):
        with patch("shutil.which", return_value="C:/fake/codex.exe"):
            assert package_service.is_codex_installed() is True

    def test_true_when_config_dir_exists_even_if_not_on_path(self, tmp_path):
        with patch("shutil.which", return_value=None), \
             patch.object(package_service.Path, "home", return_value=tmp_path):
            (tmp_path / ".codex").mkdir()
            assert package_service.is_codex_installed() is True

    def test_false_when_neither_present(self, tmp_path):
        with patch("shutil.which", return_value=None), \
             patch.object(package_service.Path, "home", return_value=tmp_path):
            assert package_service.is_codex_installed() is False


class TestFrozenExeSelfUpdate:
    def test_is_frozen_exe_false_by_default(self):
        # 테스트는 항상 일반 python 인터프리터로 돌아가므로 sys.frozen이 없다.
        assert package_service.is_frozen_exe() is False

    def test_is_frozen_exe_true_when_pyinstaller_flag_set(self):
        with patch.object(package_service.sys, "frozen", True, create=True):
            assert package_service.is_frozen_exe() is True

    def test_latest_github_release_parses_tag_and_exe_asset(self):
        fake_response = {
            "tag_name": "v0.2.0",
            "assets": [
                {"name": "LeetKitManager.exe", "browser_download_url": "https://example.com/LeetKitManager.exe"},
                {"name": "leetkit_manager-0.2.0.tar.gz", "browser_download_url": "https://example.com/x.tar.gz"},
            ],
        }
        with patch("httpx.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.json.return_value = fake_response
            result = package_service.latest_github_release()
        assert result == {
            "version": "0.2.0",
            "exe_url": "https://example.com/LeetKitManager.exe",
            "sha256_url": None,
        }

    def test_latest_github_release_exposes_checksum_asset_url(self):
        fake_response = {
            "tag_name": "v0.2.0",
            "assets": [
                {"name": "LeetKitManager.exe", "browser_download_url": "https://example.com/LeetKitManager.exe"},
                {"name": "LeetKitManager.exe.sha256", "browser_download_url": "https://example.com/sum"},
            ],
        }
        with patch("httpx.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.json.return_value = fake_response
            result = package_service.latest_github_release()
        assert result["sha256_url"] == "https://example.com/sum"

    def test_latest_github_release_tolerates_missing_checksum_asset(self):
        """체크섬 자산이 없던 옛 릴리스도 계속 업데이트할 수 있어야 한다."""
        fake_response = {
            "tag_name": "v0.2.0",
            "assets": [{"name": "LeetKitManager.exe", "browser_download_url": "https://example.com/e.exe"}],
        }
        with patch("httpx.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.json.return_value = fake_response
            result = package_service.latest_github_release()
        assert result["sha256_url"] is None

    def test_fetch_expected_sha256_parses_sha256sum_format(self):
        digest = "a" * 64
        with patch("httpx.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.text = f"{digest}  LeetKitManager.exe\n"
            assert package_service.fetch_expected_sha256("https://x") == digest

    def test_fetch_expected_sha256_parses_bare_hash(self):
        digest = "b" * 64
        with patch("httpx.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.text = digest.upper()
            assert package_service.fetch_expected_sha256("https://x") == digest

    def test_fetch_expected_sha256_rejects_garbage(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.text = "<html>404 not found</html>"
            assert package_service.fetch_expected_sha256("https://x") is None

    def test_sha256_of_file_matches_hashlib(self, tmp_path):
        import hashlib

        f = tmp_path / "blob.bin"
        f.write_bytes(b"leetkit" * 1000)
        assert package_service.sha256_of_file(f) == hashlib.sha256(b"leetkit" * 1000).hexdigest()

    def test_sha256_of_file_returns_none_for_missing_file(self, tmp_path):
        assert package_service.sha256_of_file(tmp_path / "nope.bin") is None

    def test_latest_github_release_returns_none_when_exe_asset_missing(self):
        fake_response = {"tag_name": "v0.2.0", "assets": []}
        with patch("httpx.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.json.return_value = fake_response
            result = package_service.latest_github_release()
        assert result is None

    def test_latest_github_release_returns_none_on_request_failure(self):
        with patch("httpx.get", side_effect=Exception("network down")):
            assert package_service.latest_github_release() is None

    def test_replace_running_exe_renames_backs_up_and_copies_new_content(self, tmp_path):
        current_exe = tmp_path / "LeetKitManager.exe"
        current_exe.write_bytes(b"OLD")
        new_exe = tmp_path / "downloaded.exe"
        new_exe.write_bytes(b"NEW")

        with patch.object(package_service.sys, "executable", str(current_exe)), \
             patch.object(package_service.subprocess, "Popen") as mock_popen:
            result = package_service.replace_running_exe(new_exe)

        assert result.ok is True
        mock_popen.assert_called_once()
        assert current_exe.read_bytes() == b"NEW"
        backup = tmp_path / "LeetKitManager.exe.old"
        assert backup.read_bytes() == b"OLD"

    def test_replace_running_exe_removes_stale_backup_first(self, tmp_path):
        current_exe = tmp_path / "LeetKitManager.exe"
        current_exe.write_bytes(b"OLD")
        stale_backup = tmp_path / "LeetKitManager.exe.old"
        stale_backup.write_bytes(b"STALE")
        new_exe = tmp_path / "downloaded.exe"
        new_exe.write_bytes(b"NEW")

        with patch.object(package_service.sys, "executable", str(current_exe)), \
             patch.object(package_service.subprocess, "Popen"):
            result = package_service.replace_running_exe(new_exe)

        assert result.ok is True
        assert stale_backup.read_bytes() == b"OLD"  # 옛 STALE이 아니라 방금 백업된 OLD

    def test_cleanup_old_exe_backup_noop_when_not_frozen(self, tmp_path):
        backup = tmp_path / "LeetKitManager.exe.old"
        backup.write_bytes(b"x")
        with patch.object(package_service, "is_frozen_exe", return_value=False):
            package_service.cleanup_old_exe_backup()
        assert backup.exists()  # frozen 아니면 손대지 않음

    def test_cleanup_old_exe_backup_removes_file_when_frozen(self, tmp_path):
        current_exe = tmp_path / "LeetKitManager.exe"
        backup = tmp_path / "LeetKitManager.exe.old"
        backup.write_bytes(b"x")
        with patch.object(package_service, "is_frozen_exe", return_value=True), \
             patch.object(package_service.sys, "executable", str(current_exe)):
            package_service.cleanup_old_exe_backup()
        assert not backup.exists()

    def test_cleanup_retries_when_the_file_is_briefly_locked(self, tmp_path):
        """옛 프로세스가 막 끝난 직후엔 파일이 잠깐 더 잠겨 있을 수 있다(백신이 방금
        이름 바뀐 파일을 검사 중일 때가 대표적). 한 번 시도하고 포기하면 다음 실행
        때까지 사용자 폴더에 남아 "이 파일 뭔가요?" 문의가 된다."""
        current_exe = tmp_path / "LeetKitManager.exe"
        backup = tmp_path / "LeetKitManager.exe.old"
        backup.write_bytes(b"x")

        real_unlink = Path.unlink
        calls = {"n": 0}

        def flaky_unlink(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:  # 처음 두 번은 잠겨 있다
                raise PermissionError("locked")
            return real_unlink(self, *args, **kwargs)

        with patch.object(package_service, "is_frozen_exe", return_value=True), \
             patch.object(package_service.sys, "executable", str(current_exe)), \
             patch.object(package_service, "_hide_file") as hide, \
             patch.object(Path, "unlink", flaky_unlink), \
             patch("time.sleep"):  # 테스트가 실제로 기다릴 이유는 없다
            package_service.cleanup_old_exe_backup()

        assert calls["n"] >= 3, "한 번 실패하고 포기하면 안 된다"
        assert not backup.exists()
        hide.assert_not_called()  # 지웠으면 숨길 필요가 없다

    def test_cleanup_hides_the_file_when_it_can_never_be_deleted(self, tmp_path):
        """못 지우는 것보다 나쁜 건 못 지운 걸 사용자가 보는 것이다."""
        current_exe = tmp_path / "LeetKitManager.exe"
        backup = tmp_path / "LeetKitManager.exe.old"
        backup.write_bytes(b"x")

        with patch.object(package_service, "is_frozen_exe", return_value=True), \
             patch.object(package_service.sys, "executable", str(current_exe)), \
             patch.object(package_service, "_hide_file") as hide, \
             patch.object(Path, "unlink", side_effect=PermissionError("locked forever")), \
             patch("time.sleep"):
            package_service.cleanup_old_exe_backup()

        hide.assert_called_once()

    def test_backup_is_hidden_the_moment_it_is_created(self, tmp_path):
        """교체 직후에는 이 프로세스가 살아 있어 절대 못 지운다 — 그 구간에도
        탐색기에 안 보여야 한다."""
        current_exe = tmp_path / "LeetKitManager.exe"
        current_exe.write_bytes(b"OLD")
        new_exe = tmp_path / "downloaded.exe"
        new_exe.write_bytes(b"NEW")

        with patch.object(package_service.sys, "executable", str(current_exe)), \
             patch.object(package_service.subprocess, "Popen"), \
             patch.object(package_service, "_hide_file") as hide:
            package_service.replace_running_exe(new_exe)

        hide.assert_called_once()
        assert Path(hide.call_args[0][0]).name == "LeetKitManager.exe.old"


class TestVersionComparison:
    """폴백이 예전엔 "다르면 새 버전"이었다 — 최신을 쓰는 사람에게 옛 버전으로
    내려가라고 권했다(0.1.6을 쓰는데 "0.1.5로 업데이트하세요"가 실제로 떴다)."""

    def test_newer_is_newer(self):
        assert package_service.version_gt("0.1.6", "0.1.5") is True

    def test_older_is_not_newer(self):
        assert package_service.version_gt("0.1.5", "0.1.6") is False

    def test_same_is_not_newer(self):
        assert package_service.version_gt("0.1.6", "0.1.6") is False

    def test_empty_latest_is_not_newer(self):
        """PyPI 조회 실패 시 None/빈 문자열이 온다 — 그걸로 업데이트를 권하면 안 된다."""
        assert package_service.version_gt("", "0.1.6") is False

    def test_double_digit_segments_compare_numerically(self):
        """문자열 비교로는 "0.1.10" < "0.1.9"가 된다."""
        assert package_service.version_gt("0.1.10", "0.1.9") is True
        assert package_service.version_gt("0.1.9", "0.1.10") is False

    def test_still_correct_without_packaging(self):
        """packaging은 이제 의존성에 있지만, 없는 환경에서도 안전해야 한다 —
        없어서 폴백을 탄 게 애초에 이 버그의 원인이었다."""
        import builtins

        real_import = builtins.__import__

        def no_packaging(name, *args, **kwargs):
            if name.startswith("packaging"):
                raise ImportError("packaging 없음")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", no_packaging):
            assert package_service.version_gt("0.1.5", "0.1.6") is False
            assert package_service.version_gt("0.1.6", "0.1.5") is True
            assert package_service.version_gt("0.1.10", "0.1.9") is True

    def test_unparseable_versions_never_prompt(self):
        """잘못된 업데이트 안내보다 안내를 안 하는 쪽이 낫다."""
        import builtins

        real_import = builtins.__import__

        def no_packaging(name, *args, **kwargs):
            if name.startswith("packaging"):
                raise ImportError("packaging 없음")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", no_packaging):
            assert package_service.version_gt("알수없음", "0.1.6") is False
            assert package_service.version_gt("0.1.6", "알수없음") is False


def test_packaging_is_declared_as_a_dependency():
    """선언이 빠져 있어서 맥에서만 폴백을 탔다 — 우연히 딸려오는 것에 기대면 안 된다."""
    import tomllib
    from pathlib import Path

    data = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(d.startswith("packaging") for d in data["project"]["dependencies"])


class TestLatestVersionUsesTheSameIndexAsUv:
    """Manager가 JSON API를, uv가 simple 인덱스를 보던 탓에 새 버전을 올린 직후 몇 분간
    "최신은 X"라고 판단해놓고 `uv tool install pkg==X`가 실패했다. 화면에는 이유 없이
    "실패했습니다"만 떴고, 기다리면 저절로 되니 원인을 짚기도 어려웠다."""

    def _simple(self, versions):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"name": "x", "files": [], "versions": versions}
        return resp

    def test_reads_the_simple_index(self):
        with patch("httpx.get", return_value=self._simple(["0.1.1", "0.1.2"])) as mock_get:
            assert package_service.latest_pypi_version("leetkit-manager") == "0.1.2"
        url = mock_get.call_args[0][0]
        assert "/simple/" in url, f"uv가 보는 곳을 봐야 한다: {url}"

    def test_picks_the_highest_not_the_last(self):
        """인덱스가 정렬돼 있다는 보장이 없다."""
        with patch("httpx.get", return_value=self._simple(["0.1.9", "0.1.10", "0.1.2"])):
            assert package_service.latest_pypi_version("x") == "0.1.10"

    def test_ignores_versions_it_cannot_parse(self):
        """읽을 수 없는 버전이 최신으로 뽑히면 설치할 수 없는 값을 권하게 된다."""
        with patch("httpx.get", return_value=self._simple(["0.1.2", "알수없음"])):
            assert package_service.latest_pypi_version("x") == "0.1.2"

    def test_falls_back_to_the_json_api_when_the_index_is_unreadable(self):
        """인덱스를 못 읽는다고 업데이트 확인 자체를 포기하면 안 된다."""
        simple_failed = MagicMock()
        simple_failed.raise_for_status.side_effect = Exception("보안장비 등에 막힘")
        json_api = MagicMock()
        json_api.raise_for_status.return_value = None
        json_api.json.return_value = {"info": {"version": "0.9.9"}}
        with patch("httpx.get", side_effect=[simple_failed, json_api]):
            assert package_service.latest_pypi_version("x") == "0.9.9"

    def test_returns_none_when_everything_fails(self):
        with patch("httpx.get", side_effect=Exception("오프라인")):
            assert package_service.latest_pypi_version("x") is None

    def test_tolerates_an_index_without_a_versions_field(self):
        """PEP 700 이전 형식으로 응답하는 미러도 있다."""
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"name": "x", "files": []}
        json_api = MagicMock()
        json_api.raise_for_status.return_value = None
        json_api.json.return_value = {"info": {"version": "1.2.3"}}
        with patch("httpx.get", side_effect=[resp, json_api]):
            assert package_service.latest_pypi_version("x") == "1.2.3"

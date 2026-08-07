from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

from leetkit_manager import __version__, cli


class TestVersionSingleSource:
    """버전이 두 곳에 적혀 있으면 반드시 한쪽이 뒤처진다 — 실제로 __init__.py가
    0.1.0에 멈춘 채로 0.1.5까지 나갔다. 그 결과 앱이 자기 버전을 0.1.0으로 읽어
    최신을 깔아도 항상 "업데이트 있음"이었고, 업데이트 버튼이 영영 안 사라졌다."""

    def _pyproject(self) -> dict:
        path = Path(__file__).parent.parent / "pyproject.toml"
        return tomllib.loads(path.read_text(encoding="utf-8"))

    def test_pyproject_does_not_hardcode_a_second_version(self):
        project = self._pyproject()["project"]
        assert "version" not in project, "pyproject에 버전을 직접 적으면 __init__.py와 갈라진다"
        assert "version" in project.get("dynamic", [])

    def test_pyproject_reads_the_version_from_the_package(self):
        config = self._pyproject()["tool"]["hatch"]["version"]
        assert config["path"] == "leetkit_manager/__init__.py"

    def test_package_version_looks_like_a_release(self):
        parts = __version__.split(".")
        assert len(parts) == 3 and all(p.isdigit() for p in parts), __version__


class TestWaitForExit:
    """자체 업데이트가 새 버전을 띄울 때 쓰는 숨은 인자. 옛 프로세스가 아직 살아
    있는 동안 새 프로세스가 뜨면 중복 실행 방지에 걸려 스스로 종료하고, 곧이어 옛
    프로세스도 닫히면서 아무것도 안 남는다."""

    def test_top_level_flag_parses(self):
        args = cli._build_parser().parse_args(["--wait-for-exit", "1234"])
        assert args.wait_for_exit == 1234

    def test_gui_subcommand_flag_parses(self):
        args = cli._build_parser().parse_args(["gui", "--wait-for-exit", "1234"])
        assert args.wait_for_exit == 1234

    def test_gui_waits_before_starting(self):
        args = cli._build_parser().parse_args(["gui", "--wait-for-exit", "1234"])
        with patch.object(cli, "_wait_for_pid_exit") as mock_wait, \
             patch("leetkit_manager.ui.app.run") as mock_run:
            cli._cmd_gui(args)
        mock_wait.assert_called_once_with(1234)
        mock_run.assert_called_once()

    def test_gui_does_not_wait_when_flag_is_absent(self):
        """평소 실행(바로가기 더블클릭)이 이것 때문에 느려지면 안 된다."""
        args = cli._build_parser().parse_args([])
        with patch.object(cli, "_wait_for_pid_exit") as mock_wait, \
             patch("leetkit_manager.ui.app.run"):
            cli._cmd_gui(args)
        mock_wait.assert_not_called()

    def test_wait_returns_immediately_for_a_dead_pid(self):
        with patch("psutil.pid_exists", return_value=False):
            cli._wait_for_pid_exit(999999999, timeout_s=5.0)  # 걸리면 테스트가 멈춘다

    def test_wait_gives_up_after_the_timeout(self):
        """영영 안 죽는 프로세스를 물고 늘어지면 앱이 아예 안 뜬다 — 창이 두 개 뜨는
        편이 낫다(single_instance의 판단 기준과 같은 정신)."""
        with patch("psutil.pid_exists", return_value=True):
            cli._wait_for_pid_exit(1234, timeout_s=0.5)

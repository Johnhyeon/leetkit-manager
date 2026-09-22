from __future__ import annotations

import sys

import pytest

from leetkit_manager.process_runner import run_cli, run_json_cli


def test_run_cli_captures_stdout_and_exit_code():
    result = run_cli([sys.executable, "-c", "print('hello')"])
    assert result.ok is True
    assert result.exit_code == 0
    assert "hello" in result.stdout


def test_run_cli_nonzero_exit_is_not_ok():
    result = run_cli([sys.executable, "-c", "import sys; sys.exit(1)"])
    assert result.ok is False
    assert result.exit_code == 1


def test_run_cli_times_out_without_hanging_forever():
    result = run_cli([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
    assert result.timed_out is True
    assert result.error == "timeout"
    assert result.ok is False


def test_run_cli_missing_command_reports_not_found():
    result = run_cli(["this-command-does-not-exist-xyz"])
    assert result.error == "not_found"
    assert result.ok is False


def test_run_cli_input_text_is_piped_via_stdin_not_argv():
    secret = "MY-SECRET-KEY-VALUE"
    cmd = [sys.executable, "-c", "import sys; print(sys.stdin.read().strip())"]
    result = run_cli(cmd, input_text=secret)
    assert secret in result.stdout
    # 커맨드 자체(cmd 리스트)에는 원문이 없어야 한다 — argv로 넘기지 않았다는 증거.
    assert secret not in result.cmd


def test_run_json_cli_parses_stdout_as_json():
    cmd = [sys.executable, "-c", "print('{\"ok\": true, \"n\": 1}')"]
    result, payload = run_json_cli(cmd)
    assert result.ok is True
    assert payload == {"ok": True, "n": 1}


def test_run_json_cli_returns_none_payload_on_invalid_json_without_raising():
    cmd = [sys.executable, "-c", "print('this is not json')"]
    result, payload = run_json_cli(cmd)
    assert result.ok is True  # 프로세스 자체는 정상 종료
    assert payload is None  # 파싱만 실패


class TestLaunchBlockedByWindowsPolicy:
    """Windows 스마트 앱 제어(WDAC)가 서명 없는 Lens 실행 파일을 막으면 CreateProcess가
    OSError [WinError 4551]로 실패한다. 예전엔 이 예외가 run_cli를 그대로 뚫고 나가
    진단 전체를 끊고, 화면에는 원문만 남았다(실제 고객 문의). 결과값으로 잡아야 한다."""

    @staticmethod
    def _raise(exc):
        def _run(*args, **kwargs):
            raise exc

        return _run

    def test_generic_launch_oserror_is_a_result_not_an_exception(self, monkeypatch):
        monkeypatch.setattr("subprocess.run", self._raise(OSError(13, "denied")))
        result = run_cli(["whatever"])
        assert result.ok is False
        assert result.error == "launch_failed"
        assert "denied" in result.stderr

    @pytest.mark.skipif(sys.platform != "win32", reason="winerror는 Windows에만 있다")
    def test_policy_block_gets_its_own_error_name(self, monkeypatch):
        blocked = OSError(0, "An Application Control policy has blocked this file", None, 4551)
        monkeypatch.setattr("subprocess.run", self._raise(blocked))
        result = run_cli(["stocklens-doctor", "--json"])
        assert result.error == "blocked"  # not_found도 timeout도 아니다 — 할 일이 다르다
        assert result.ok is False

    def test_missing_command_still_reports_not_found(self, monkeypatch):
        """FileNotFoundError도 OSError의 자식이다 — 새 except가 먼저 삼키면 안 된다."""
        monkeypatch.setattr("subprocess.run", self._raise(FileNotFoundError(2, "no such file")))
        assert run_cli(["nope"]).error == "not_found"


class TestStreamingTimeout:
    """`run_cli_streaming`의 timeout은 **출력이 한 줄도 안 와도** 걸려야 한다.

    예전 구현은 `for line in proc.stdout:` 루프 안에서만 남은 시간을 쟀다 — 다음 줄이
    와야 시간을 보는 구조라, 조용히 멈춘 자식(락 대기·네트워크 정지 등) 앞에서는
    timeout 검사에 영영 도달하지 못했다. 이 함수는 `uv tool install`(Lens 설치·업데이트·
    Manager 자기 업데이트)의 유일한 실행 경로라, 여기서 매달리면 화면은 "…하는 중"
    오버레이가 걸린 채 끝나지 않는다.
    """

    def test_silent_child_still_times_out(self):
        from leetkit_manager.process_runner import run_cli_streaming

        result = run_cli_streaming(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1.0
        )
        assert result.timed_out is True
        assert result.error == "timeout"
        assert result.ok is False
        assert result.duration_s < 10  # 30초까지 안 기다렸다는 증거

    def test_lines_still_stream_and_exit_code_survives(self):
        from leetkit_manager.process_runner import run_cli_streaming

        seen: list[str] = []
        result = run_cli_streaming(
            [sys.executable, "-c", "print('a'); print('b')"],
            timeout=20.0,
            on_line=seen.append,
        )
        assert result.ok is True
        assert result.exit_code == 0
        assert seen == ["a", "b"]
        assert "a" in result.stdout and "b" in result.stdout

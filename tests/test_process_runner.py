from __future__ import annotations

import sys

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

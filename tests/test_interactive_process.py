"""interactive_process.py 단위 테스트 — 실제 파이썬 서브프로세스(echo 스크립트)로
스레드+큐 기반 stdin/stdout 왕복이 실제로 동작하는지 검증한다. TelegramLens/Telethon에는
전혀 의존하지 않는다(이 모듈은 범용 primitive라 그걸로 테스트할 필요가 없음)."""

from __future__ import annotations

import sys

from leetkit_manager.interactive_process import InteractiveProcess

_ECHO_SCRIPT = (
    "import json, sys\n"
    "print(json.dumps({'status': 'ready'}), flush=True)\n"
    "for line in sys.stdin:\n"
    "    msg = json.loads(line)\n"
    "    print(json.dumps({'status': 'echo', 'received': msg}), flush=True)\n"
)

_SLOW_SCRIPT = (
    "import sys, time\n"
    "print('{\"status\": \"ready\"}', flush=True)\n"
    "for line in sys.stdin:\n"
    "    time.sleep(3)\n"
    "    print('{\"status\": \"late\"}', flush=True)\n"
)

_SILENT_EXIT_SCRIPT = "pass\n"


def test_read_first_gets_initial_status_before_any_send():
    proc = InteractiveProcess([sys.executable, "-c", _ECHO_SCRIPT])
    try:
        first = proc.read_first(timeout=5)
        assert first == {"status": "ready"}
    finally:
        proc.close()


def test_send_writes_and_reads_next_line():
    proc = InteractiveProcess([sys.executable, "-c", _ECHO_SCRIPT])
    try:
        proc.read_first(timeout=5)
        reply = proc.send({"phone": "+821012345678"}, timeout=5)
        assert reply == {"status": "echo", "received": {"phone": "+821012345678"}}
    finally:
        proc.close()


def test_multiple_sends_are_independent_round_trips():
    proc = InteractiveProcess([sys.executable, "-c", _ECHO_SCRIPT])
    try:
        proc.read_first(timeout=5)
        r1 = proc.send({"step": 1}, timeout=5)
        r2 = proc.send({"step": 2}, timeout=5)
        assert r1["received"] == {"step": 1}
        assert r2["received"] == {"step": 2}
    finally:
        proc.close()


def test_send_times_out_returns_none_without_hanging():
    proc = InteractiveProcess([sys.executable, "-c", _SLOW_SCRIPT])
    try:
        proc.read_first(timeout=5)
        reply = proc.send({"anything": True}, timeout=0.5)
        assert reply is None
    finally:
        proc.close()


def test_process_exiting_without_output_yields_none():
    proc = InteractiveProcess([sys.executable, "-c", _SILENT_EXIT_SCRIPT])
    try:
        first = proc.read_first(timeout=5)
        assert first is None
    finally:
        proc.close()


def test_close_terminates_process():
    proc = InteractiveProcess([sys.executable, "-c", _ECHO_SCRIPT])
    proc.read_first(timeout=5)
    proc.close()
    # 종료 후 대기하면 반환 코드가 정상적으로 잡혀야 한다(고아 프로세스로 안 남음).
    assert proc._proc.poll() is not None

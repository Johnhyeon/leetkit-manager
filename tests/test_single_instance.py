from __future__ import annotations

import os
import sys
from unittest.mock import patch

import psutil
import pytest

from leetkit_manager import single_instance


@pytest.fixture(autouse=True)
def _reset_mutex_state():
    """테스트 사이에 뮤텍스가 남지 않게 *실제로 닫는다*.

    전역 변수만 None으로 비우면 OS 핸들은 그대로 열려 있어 프로세스가 끝날 때까지
    뮤텍스가 점유된다 — 그러면 뒤따르는 테스트가 "이미 실행 중"으로 잘못 판정한다
    (전체 스위트를 돌릴 때만 깨져서 실제로 한 번 걸렸다)."""
    single_instance.release()
    single_instance._mutex_handle = None
    yield
    single_instance.release()
    single_instance._mutex_handle = None


class TestFallbackLockFile:
    """뮤텍스를 못 쓰는 환경(비Windows·pywin32 부재)용 경로."""

    @pytest.fixture(autouse=True)
    def _force_fallback(self):
        with patch.object(single_instance, "_try_acquire_mutex", return_value=None):
            yield

    def test_not_running_when_no_lock_file(self, tmp_path):
        with patch.object(single_instance, "_lock_path", return_value=tmp_path / "app.lock"):
            assert single_instance.is_already_running() is False

    def test_running_when_lock_holds_current_process(self, tmp_path):
        lock = tmp_path / "app.lock"
        with patch.object(single_instance, "_lock_path", return_value=lock):
            single_instance.acquire()
            assert single_instance.is_already_running() is True
            single_instance.release()
            assert not lock.exists()

    def test_not_running_when_lock_pid_is_dead(self, tmp_path):
        lock = tmp_path / "app.lock"
        lock.write_text('{"pid": 999999999}', encoding="utf-8")
        with patch.object(single_instance, "_lock_path", return_value=lock):
            assert single_instance.is_already_running() is False

    def test_not_running_when_lock_file_is_corrupt(self, tmp_path):
        lock = tmp_path / "app.lock"
        lock.write_text("not json", encoding="utf-8")
        with patch.object(single_instance, "_lock_path", return_value=lock):
            assert single_instance.is_already_running() is False

    def test_pid_reuse_does_not_permanently_block_launch(self, tmp_path):
        """실사용에서 확인된 최악의 시나리오: 강제 종료로 락이 남고, 그 PID를 다른
        python 프로세스(Claude Desktop이 띄우는 MCP 서버가 정확히 python.exe)가
        물려받으면 앱이 영영 안 떴다. 생성 시각이 다르면 다른 프로세스로 본다."""
        lock = tmp_path / "app.lock"
        my_pid = os.getpid()
        real_start = psutil.Process(my_pid).create_time()
        # 같은 PID지만 "훨씬 예전에 시작된" 것으로 기록된 락 = 재사용된 PID
        lock.write_text(
            f'{{"pid": {my_pid}, "started_at": {real_start - 9999}}}', encoding="utf-8"
        )
        with patch.object(single_instance, "_lock_path", return_value=lock):
            assert single_instance.is_already_running() is False

    def test_same_pid_and_same_start_time_is_treated_as_running(self, tmp_path):
        lock = tmp_path / "app.lock"
        my_pid = os.getpid()
        real_start = psutil.Process(my_pid).create_time()
        lock.write_text(f'{{"pid": {my_pid}, "started_at": {real_start}}}', encoding="utf-8")
        with patch.object(single_instance, "_lock_path", return_value=lock):
            assert single_instance.is_already_running() is True


@pytest.mark.skipif(sys.platform != "win32", reason="명명된 뮤텍스는 Windows 전용")
class TestNamedMutex:
    @pytest.fixture(autouse=True)
    def _unique_mutex_name(self, request):
        """테스트마다 다른 뮤텍스 이름을 쓴다. 이름이 같으면 앞선 테스트가 흘린
        핸들이나 실제로 떠 있는 Manager 때문에 결과가 바뀐다 — 프로세스 전역 상태에
        기대지 않게 격리한다(전체 스위트에서만 깨지는 문제로 실제 한 번 걸렸다)."""
        name = f"Local\\LeetKitManagerTest-{request.node.name}"
        with patch.object(single_instance, "_MUTEX_NAME", name):
            yield

    def test_first_instance_acquires_and_second_is_blocked(self):
        """뮤텍스는 커널이 프로세스 종료 시 자동 해제하므로 잔재가 남지 않는다 —
        락 파일 방식의 '강제 종료 후 영영 안 뜸' 문제가 원천적으로 사라진다."""
        assert single_instance.is_already_running() is False  # 첫 인스턴스 = 소유권 확보
        first_handle = single_instance._mutex_handle
        assert first_handle is not None

        # 같은 이름으로 다시 열면 "이미 존재"로 잡힌다(다른 인스턴스 흉내)
        assert single_instance._try_acquire_mutex() is False
        single_instance.release()  # 방금 연 두 번째 핸들 정리

        single_instance._mutex_handle = first_handle
        single_instance.release()
        assert single_instance._mutex_handle is None

    def test_release_is_safe_to_call_twice(self):
        single_instance.is_already_running()
        single_instance.release()
        single_instance.release()  # 두 번째 호출이 터지면 안 된다


class TestNotifyAlreadyRunning:
    def test_prefers_focusing_existing_window(self):
        """중복 실행 시 사용자가 기대하는 건 '아무 반응 없음'이 아니라 기존 창이
        앞으로 나오는 것이다."""
        with patch.object(single_instance, "focus_existing_window", return_value=True) as mock_focus:
            single_instance.notify_already_running()
        mock_focus.assert_called_once()

    @pytest.mark.skipif(sys.platform != "win32", reason="MessageBox 폴백은 Windows 전용")
    def test_falls_back_to_message_box_when_focus_fails(self):
        """창 없는 exe에서는 stderr가 None이라 print가 사라진다 — 사용자 눈엔
        '아이콘을 눌러도 무반응'이 되므로 최소한 메시지 상자는 떠야 한다."""
        import ctypes

        with patch.object(single_instance, "focus_existing_window", return_value=False), \
             patch.object(ctypes, "windll", create=True) as mock_windll:
            single_instance.notify_already_running()
        mock_windll.user32.MessageBoxW.assert_called_once()

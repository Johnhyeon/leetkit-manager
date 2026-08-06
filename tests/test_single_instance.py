from __future__ import annotations

from unittest.mock import patch

from leetkit_manager import single_instance


def test_not_running_when_no_lock_file(tmp_path):
    with patch.object(single_instance, "_lock_path", return_value=tmp_path / "app.lock"):
        assert single_instance.is_already_running() is False


def test_running_when_lock_holds_current_process_pid(tmp_path):
    lock = tmp_path / "app.lock"
    with patch.object(single_instance, "_lock_path", return_value=lock):
        single_instance.acquire()
        assert single_instance.is_already_running() is True
        single_instance.release()
        assert not lock.exists()


def test_not_running_when_lock_pid_is_dead(tmp_path):
    lock = tmp_path / "app.lock"
    lock.write_text('{"pid": 999999999}', encoding="utf-8")
    with patch.object(single_instance, "_lock_path", return_value=lock):
        assert single_instance.is_already_running() is False


def test_not_running_when_lock_file_is_corrupt(tmp_path):
    lock = tmp_path / "app.lock"
    lock.write_text("not json", encoding="utf-8")
    with patch.object(single_instance, "_lock_path", return_value=lock):
        assert single_instance.is_already_running() is False

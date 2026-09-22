"""Manager 실행 기록의 계약.

이 기록이 생긴 이유는 하나다 — 맥에서 "업데이트 후 앱을 다시 시작하는 중에서 멈춘다"는
보고를 받고도, Manager가 아무것도 안 남겨서 어느 단계에서 멈췄는지 확인할 방법이 없었다.
그래서 여기서 지키는 건 두 가지다: (1) 멈춘 자리를 알 수 있게 시작 줄이 먼저 나간다,
(2) 그 기록이 지원 문의로 그대로 나가므로 비밀이 섞이지 않는다.
"""

from __future__ import annotations

import pytest

from leetkit_manager import applog


@pytest.fixture
def log_home(tmp_path, monkeypatch):
    """모듈 전역 상태(열린 파일 하나)를 매 테스트마다 초기화한다."""
    folder = tmp_path / "logs"
    monkeypatch.setattr(applog, "log_dir", lambda: folder)
    monkeypatch.setattr(applog, "_path", None)
    monkeypatch.setattr(applog, "_written", 0)
    monkeypatch.setattr(applog, "_disabled", False)
    yield folder


def _text(folder):
    files = list(folder.glob("manager-*.log"))
    assert len(files) == 1, f"기록 파일이 하나여야 한다: {files}"
    return files[0].read_text(encoding="utf-8")


class TestRedaction:
    """기록은 지원 문의로 그대로 나간다 — 부르는 쪽이 매번 기억해야 하는 규칙은
    언젠가 새므로, 통과 지점을 applog 하나로 두고 여기서 막는다."""

    def test_license_key_shaped_value_is_masked(self, log_home):
        secret = "ABCDEFGH23456723ABCDEFGH23456723ABCDEFGH"
        applog.setup()
        applog.event("activate", key=secret)
        assert secret not in _text(log_home)

    def test_named_secret_parameter_is_masked(self, log_home):
        dart_key = "3c209ef8fb073daadf3bcee13811c7541af6cebf"
        applog.setup()
        applog.event("run.begin", cmd=f"dartlens --crtfc_key={dart_key}")
        assert dart_key not in _text(log_home)

    def test_home_username_is_masked(self, log_home):
        applog.setup()
        applog.event("start", executable="/Users/johnhyeon/.local/bin/leetkit-manager")
        text = _text(log_home)
        assert "johnhyeon" not in text
        assert "<user>" in text


class TestNeverBreaksTheApp:
    """기록을 못 남기는 것보다 나쁜 건 기록 때문에 앱이 안 뜨는 것이다."""

    def test_event_before_setup_is_a_noop(self, log_home):
        applog.event("something", a=1)  # 예외가 나면 안 된다
        assert not list(log_home.glob("*.log"))

    def test_setup_failure_disables_quietly(self, tmp_path, monkeypatch):
        blocked = tmp_path / "nope"
        blocked.write_text("파일이라 폴더를 못 만든다", encoding="utf-8")
        monkeypatch.setattr(applog, "log_dir", lambda: blocked / "logs")
        monkeypatch.setattr(applog, "_path", None)
        monkeypatch.setattr(applog, "_disabled", False)

        applog.setup()
        applog.event("after", x=1)

        assert applog.current_path() is None

    def test_setup_is_idempotent(self, log_home):
        applog.setup()
        first = applog.current_path()
        applog.setup()
        assert applog.current_path() == first


class TestStartLineComesFirst:
    """끝 줄만 남기면 **매달린 호출은 기록에 아무 흔적도 안 남는다** — 그게 정확히
    찾고 싶은 상황이라, 시작 줄이 먼저 나가는 건 이 모듈의 존재 이유 그 자체다."""

    def test_timed_writes_begin_before_the_body_runs(self, log_home):
        applog.setup()
        seen_mid_run = {}
        with applog.timed("install", package="stocklens"):
            seen_mid_run["text"] = _text(log_home)
        assert "install.begin" in seen_mid_run["text"]
        assert "install.end" not in seen_mid_run["text"]
        assert "install.end" in _text(log_home)

    def test_timed_records_failures_instead_of_swallowing_them(self, log_home):
        applog.setup()
        with pytest.raises(RuntimeError):
            with applog.timed("install"):
                raise RuntimeError("boom")
        text = _text(log_home)
        assert "install.failed" in text
        assert "RuntimeError: boom" in text


class TestPruning:
    def test_only_the_most_recent_runs_are_kept(self, log_home, monkeypatch):
        log_home.mkdir(parents=True)
        for i in range(applog._KEEP_FILES + 5):
            (log_home / f"manager-2026010{i:02d}-000000-{i}.log").write_text("x", encoding="utf-8")
        applog.setup()
        assert len(list(log_home.glob("manager-*.log"))) == applog._KEEP_FILES


class TestSubprocessLoggingLeavesBodiesOut:
    """process_runner 가 남기는 건 '무엇을 언제 얼마나'뿐이다. stdin 원문과 자식의
    stdout·stderr 본문은 라이선스 키가 들어올 수 있는 유일한 통로라 절대 안 남긴다."""

    def test_stdin_and_stdout_never_reach_the_log(self, log_home):
        import sys as _sys

        from leetkit_manager.process_runner import run_cli

        applog.setup()
        secret = "ABCDEFGH23456723ABCDEFGH23456723ABCDEFGH"
        # 자식은 stdin 으로 받은 키와, 커맨드에는 글자 그대로 안 적힌 표식을 함께 뱉는다.
        result = run_cli(
            [_sys.executable, "-c", "import sys; print('MARK' + 'ER77'); sys.stdin.read()"],
            input_text=secret,
            timeout=20,
        )
        assert "MARKER77" in result.stdout  # 자식은 분명히 뱉었는데
        text = _text(log_home)
        assert secret not in text  # stdin 원문은 기록에 없다
        assert "MARKER77" not in text  # 자식이 뱉은 본문도 기록에 없다
        assert "run.begin" in text and "run.end" in text


class TestThreadCrashesAreRecorded:
    """2026-09-22 맥: pywebview 로컬 서버 스레드가 포트 충돌로 죽었는데
    (OSError: [Errno 48] Address already in use) 앱은 그대로 살아서 빈 창을 띄웠다.
    스레드 예외는 main() 의 크래시 기록에 안 걸리고, .app 번들은 stderr 도 없어
    어디에도 흔적이 안 남았다."""

    def test_exception_in_a_thread_lands_in_the_log(self, log_home, monkeypatch):
        import threading

        monkeypatch.setattr(applog, "_thread_hook_installed", False)
        monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)
        applog.setup()

        def boom():
            raise OSError(48, "Address already in use")

        t = threading.Thread(target=boom, name="Thread-1 (<lambda>)")
        t.start()
        t.join()

        text = _text(log_home)
        assert "thread.crash" in text
        assert "Address already in use" in text
        assert "Thread-1" in text

    def test_previous_hook_still_runs(self, log_home, monkeypatch):
        """터미널에서 띄운 경우에는 여전히 화면에도 보여야 한다 — 기록으로 갈음하지 않는다."""
        import threading

        seen = []
        monkeypatch.setattr(applog, "_thread_hook_installed", False)
        monkeypatch.setattr(threading, "excepthook", lambda args: seen.append(args))
        applog.setup()

        t = threading.Thread(target=lambda: 1 / 0)
        t.start()
        t.join()

        assert seen, "원래 훅이 안 불렸다"

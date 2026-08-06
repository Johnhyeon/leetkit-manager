"""GUI 중복 실행 방지 — `leetkit-manager gui`를 두 번 실행해도 창이 두 개 뜨지 않게 한다.

Windows에서는 **명명된 뮤텍스**를 쓴다. 예전엔 PID를 적은 락 파일이었는데 실사용에서
두 가지가 문제였다:

1. **PID 재사용** — 작업관리자로 강제 종료하면 락 파일이 남는데(정상 종료 경로를 안 타서),
   그 PID를 다른 python 프로세스가 물려받으면(Claude Desktop이 띄우는 MCP 서버가 정확히
   python.exe다) 앱이 "이미 실행 중"으로 판정돼 **영영 안 뜬다**. 복구하려면 사용자가
   숨은 폴더의 락 파일을 직접 지워야 하는데, 그걸 알 방법이 없다.
2. **TOCTOU** — 검사와 기록 사이에 틈이 있어 바로가기를 빠르게 두 번 누르면 창이 두 개
   뜨고, 나중 것이 먼저 닫히면서 살아있는 인스턴스의 락 파일을 지워버렸다.

뮤텍스는 커널이 프로세스 종료 시(강제 종료 포함) 자동으로 해제하므로 잔재가 원천적으로
남지 않고, 생성 자체가 원자적이라 TOCTOU도 없다. 뮤텍스를 못 쓰는 환경(비Windows,
pywin32 부재)에서는 예전 락 파일 방식으로 물러난다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psutil

# 사용자별로 구분되도록 Local\ 네임스페이스를 쓴다(Global\은 관리자 권한이 필요할 수 있고,
# 다중 사용자 PC에서 서로의 실행을 막아버린다).
_MUTEX_NAME = "Local\\LeetKitManager-SingleInstance"
_WINDOW_TITLE = "LeetKit Manager"

_mutex_handle = None


def _lock_path() -> Path:
    d = Path.home() / ".leetkit-manager"
    d.mkdir(parents=True, exist_ok=True)
    return d / "app.lock"


def _try_acquire_mutex() -> bool | None:
    """뮤텍스 획득 시도. True=내가 첫 인스턴스, False=이미 떠 있음, None=이 방식 못 씀."""
    if sys.platform != "win32":
        return None
    try:
        import win32api
        import win32event
        import winerror
    except Exception:
        return None

    global _mutex_handle
    try:
        _mutex_handle = win32event.CreateMutex(None, False, _MUTEX_NAME)
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            return False
        return True
    except Exception:
        _mutex_handle = None
        return None


def focus_existing_window() -> bool:
    """이미 떠 있는 창을 앞으로 가져온다. 중복 실행 시 "아무 반응 없음"이 아니라
    기존 창이 뜨는 게 사용자가 기대하는 동작이다(아이콘을 다시 눌렀을 때)."""
    if sys.platform != "win32":
        return False
    try:
        import win32con
        import win32gui
    except Exception:
        return False

    found = []

    def _enum(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == _WINDOW_TITLE:
            found.append(hwnd)

    try:
        win32gui.EnumWindows(_enum, None)
        if not found:
            return False
        hwnd = found[0]
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def notify_already_running() -> None:
    """창 없는 exe에서는 stderr가 아예 없어서(sys.stderr is None) print가 조용히
    사라진다 — 사용자 눈엔 "아이콘을 눌러도 아무 일도 안 일어남"이 된다.
    기존 창을 띄우는 걸 먼저 시도하고, 그것도 안 되면 최소한 메시지 상자라도 보여준다."""
    if focus_existing_window():
        return
    message = "LeetKit Manager가 이미 실행 중입니다.\n기존 창을 확인해주세요."
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "LeetKit Manager", 0x40)  # MB_ICONINFORMATION
            return
        except Exception:
            pass
    elif sys.platform == "darwin":
        # macOS도 창 없이 뜬 앱에서는 stderr가 아무 데도 안 보인다 — 시스템 알림창으로.
        try:
            import subprocess

            script = f'display alert "LeetKit Manager" message "{message}"'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)
            return
        except Exception:
            pass
    if sys.stderr:
        print("LeetKit Manager가 이미 실행 중입니다 — 기존 창을 확인하세요.", file=sys.stderr)


def is_already_running() -> bool:
    """다른 인스턴스가 떠 있는지. Windows에서는 뮤텍스 생성 결과가 곧 답이며,
    이 호출이 성공하면 소유권까지 확보한 상태라 별도 acquire가 필요 없다."""
    acquired = _try_acquire_mutex()
    if acquired is not None:
        return not acquired
    return _stale_safe_lock_says_running()


def _stale_safe_lock_says_running() -> bool:
    """뮤텍스를 못 쓰는 환경용 폴백. PID만 보던 예전 방식의 오탐(=앱이 영영 안 뜸)을
    줄이려고 프로세스 생성 시각까지 대조한다 — PID가 재사용됐다면 생성 시각이
    락을 남긴 시점보다 뒤이므로 다른 프로세스임을 알 수 있다."""
    path = _lock_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid = data.get("pid")
        started_at = data.get("started_at")
    except Exception:
        return False
    if not pid or not psutil.pid_exists(pid):
        return False
    try:
        proc = psutil.Process(pid)
        if started_at is not None and abs(proc.create_time() - started_at) > 1.0:
            return False  # PID는 같지만 다른(나중에 뜬) 프로세스 — 잔재로 본다
        name = proc.name().lower()
        return "python" in name or "leetkit" in name
    except Exception:
        return False


def acquire() -> None:
    """폴백 경로에서만 의미가 있다(뮤텍스는 is_already_running에서 이미 확보됨).
    프로세스 생성 시각을 같이 적어 PID 재사용을 구분할 수 있게 한다."""
    if _mutex_handle is not None:
        return
    try:
        started_at = psutil.Process(os.getpid()).create_time()
    except Exception:
        started_at = None
    _lock_path().write_text(
        json.dumps({"pid": os.getpid(), "started_at": started_at}), encoding="utf-8"
    )


def release() -> None:
    global _mutex_handle
    if _mutex_handle is not None:
        try:
            import win32api

            win32api.CloseHandle(_mutex_handle)
        except Exception:
            pass
        _mutex_handle = None
        return
    try:
        _lock_path().unlink(missing_ok=True)
    except Exception:
        pass

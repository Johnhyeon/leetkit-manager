"""GUI 중복 실행 방지 — `leetkit-manager gui`를 두 번 실행해도 창이 두 개 뜨지 않게 한다.

TelegramLens의 DaemonLock과 같은 정신(락 파일 + PID 확인)이지만, 여기는 좀비 프로세스를
자동으로 죽이거나 하지 않는다 — 그냥 "이미 떠 있으면 새 창을 열지 않는다"만 한다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psutil


def _lock_path() -> Path:
    d = Path.home() / ".leetkit-manager"
    d.mkdir(parents=True, exist_ok=True)
    return d / "app.lock"


def is_already_running() -> bool:
    """락 파일에 적힌 PID가 지금도 살아있는 leetkit-manager 프로세스인지."""
    path = _lock_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid = data.get("pid")
    except Exception:
        return False
    if not pid or not psutil.pid_exists(pid):
        return False
    try:
        # PID 재사용(그 사이 완전히 다른 프로그램이 같은 PID를 받은 경우)까지는 아니어도,
        # 최소한 파이썬 프로세스가 맞는지 정도는 확인해 오탐 가능성을 줄인다.
        name = psutil.Process(pid).name().lower()
        return "python" in name or "leetkit" in name
    except Exception:
        return False


def acquire() -> None:
    _lock_path().write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")


def release() -> None:
    try:
        _lock_path().unlink(missing_ok=True)
    except Exception:
        pass

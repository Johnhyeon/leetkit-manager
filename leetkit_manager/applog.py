"""Manager가 자기 실행 기록을 남기는 곳 — 고객 PC에서 무슨 일이 있었는지 볼 유일한 창.

**왜 생겼나.** 2026-09-22 맥에서 "업데이트 후 앱을 다시 시작하는 중에서 멈춘다"는 보고를
받았는데, Manager는 그때까지 아무 기록도 남기지 않았다. 지원 번들도 Lens 로그만 모은다.
그래서 고객이 무엇을 보내줘도 "창을 닫으라고 했는데 안 닫혔다"의 어느 단계에서 멈췄는지
알 방법이 없었다 — 재현기가 없는 다른 OS의 문제는 이 상태로는 영영 못 짚는다.

**설계 결정 세 가지.**

1. **실행 한 번에 파일 하나** (`manager-<날짜>-<시각>-<pid>.log`). 자기 업데이트는 옛
   프로세스와 새 프로세스가 몇 초 겹쳐 돈다 — 한 파일을 둘이 쓰면 회전(rename)이 서로를
   밟고, 윈도우에서는 상대가 열어둔 파일을 못 바꿔 예외가 난다. 나눠 쓰면 경합이 없고,
   받아보는 쪽도 "옛 프로세스가 어디까지 갔고 새 프로세스가 언제 떴나"를 나란히 볼 수
   있다(이 문제에서 정확히 필요한 그림이다). 오래된 파일은 setup에서 정리한다.
2. **값은 전부 redaction을 통과시킨다.** 기록은 지원 문의로 그대로 밖에 나간다. 라이선스
   키·API 키·전화번호·홈 경로 사용자명은 여기서 한 번 더 지운다.
3. **어떤 일이 있어도 앱으로 예외를 올리지 않는다.** 기록을 못 남기는 것보다 나쁜 건
   기록 때문에 앱이 안 뜨는 것이다. 디스크가 꽉 찼든 권한이 막혔든 조용히 포기한다.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from leetkit_manager import redaction

# 실행 하나가 남길 수 있는 최대 크기. 여기 담기는 건 사람이 읽는 사건 줄뿐이라 정상
# 사용에서는 몇 KB로 끝난다 — 이 한도는 무언가 폭주했을 때 디스크를 채우지 않기 위한
# 안전장치다. 넘어가면 한 줄 남기고 그 실행은 더 안 쓴다.
_MAX_BYTES = 1_000_000
# 남겨둘 지난 실행 기록 수. 자기 업데이트 한 번에 파일이 둘 생기므로(옛·새 프로세스)
# 최근 몇 번의 실행을 보려면 이 정도는 있어야 한다.
_KEEP_FILES = 12

_lock = threading.Lock()
_path: Path | None = None
_written = 0
_disabled = False


def log_dir() -> Path:
    return Path.home() / ".leetkit-manager" / "logs"


def current_path() -> Path | None:
    """이번 실행이 쓰고 있는 파일. setup 전이거나 못 열었으면 None."""
    return _path


def recent_files(limit: int = _KEEP_FILES) -> list[Path]:
    """최근 실행 기록 파일들(새 것부터). 지원 번들이 담아갈 목록이다."""
    try:
        files = sorted(log_dir().glob("manager-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    return files[:limit]


def setup(role: str = "gui") -> None:
    """이번 실행의 기록 파일을 연다. 두 번 불러도 처음 것을 그대로 쓴다."""
    global _path, _disabled
    with _lock:
        if _path is not None or _disabled:
            return
        try:
            folder = log_dir()
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            _path = folder / f"manager-{stamp}-{os.getpid()}.log"
            _path.touch()
        except Exception:
            _path = None
            _disabled = True  # 한 번 실패하면 매 줄마다 다시 시도하지 않는다
            return
    _prune()
    event(
        "start",
        role=role,
        version=_version(),
        platform=sys.platform,
        frozen=bool(getattr(sys, "frozen", False)),
        python=sys.version.split()[0],
        executable=sys.executable,
        pid=os.getpid(),
    )


def _version() -> str:
    try:
        from leetkit_manager import __version__

        return __version__
    except Exception:
        return "?"


def _prune() -> None:
    try:
        files = sorted(log_dir().glob("manager-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[_KEEP_FILES:]:
            stale.unlink(missing_ok=True)
    except Exception:
        pass


def _render(value: object) -> str:
    text = "-" if value is None else str(value)
    text = text.replace("\n", "\n").replace("\r", "")
    try:
        text = redaction.redact(text)
    except Exception:
        return "[REDACT-FAILED]"  # 마스킹이 안 되면 원문을 내보내느니 값을 버린다
    return text if text else '""'


def event(name: str, **fields: object) -> None:
    """사건 한 줄. 이름은 `단계.무슨일`(예: `quit.destroy.returned`) 꼴로 쓴다.

    값에 비밀이 섞일 수 있다는 전제로 전부 마스킹한다 — 부르는 쪽이 매번 기억해야 하는
    규칙은 언젠가 새기 마련이라, 통과 지점을 여기 하나로 둔다."""
    global _written
    if _path is None or _disabled:
        return
    line = " ".join(f"{k}={_render(v)}" for k, v in fields.items())
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"{stamp} | {name}{' | ' + line if line else ''}\n"
    with _lock:
        if _written > _MAX_BYTES:
            return
        try:
            with open(_path, "a", encoding="utf-8") as f:
                if _written <= _MAX_BYTES < _written + len(text):
                    f.write(f"{stamp} | log.truncated | 이번 실행 기록이 한도를 넘어 여기서 멈춘다\n")
                else:
                    f.write(text)
        except Exception:
            return
        _written += len(text)


class timed:
    """`with applog.timed("install", package=...):` — 들어갈 때와 나올 때를 같이 남긴다.

    멈춘 자리를 찾는 게 목적이라 **시작 줄이 반드시 먼저 나가야 한다.** 끝 줄만 남기면
    매달린 호출은 아무 흔적도 안 남는다 — 그게 이 모듈이 생긴 이유다."""

    def __init__(self, name: str, **fields: object) -> None:
        self._name = name
        self._fields = fields
        self._started = 0.0

    def __enter__(self) -> "timed":
        self._started = time.monotonic()
        event(f"{self._name}.begin", **self._fields)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        elapsed = f"{time.monotonic() - self._started:.1f}s"
        if exc_type is None:
            event(f"{self._name}.end", elapsed=elapsed, **self._fields)
        else:
            event(f"{self._name}.failed", elapsed=elapsed, error=f"{exc_type.__name__}: {exc}", **self._fields)
        return False

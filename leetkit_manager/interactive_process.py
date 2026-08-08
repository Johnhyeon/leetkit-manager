"""여러 번 대화가 필요한 자식 프로세스 실행 — `process_runner.run_cli`(한 번 실행→결과
받고 끝)로는 표현 못 하는 "오래 살려두고 stdin/stdout으로 JSON 한 줄씩 주고받는" 상호작용
전용. 지금은 TelegramLens 로그인 마법사(전화번호 → SMS 코드 → 필요하면 2단계 인증)가
유일한 사용처다.

사용자가 문자를 확인하고 입력하는 "사람이 기다리는 시간"은 두 `send()` 호출 "사이"에
있다 — 그 시간 동안 이 모듈은 아무것도 블록하지 않는다(자식 프로세스가 조용히 자기
stdin에서 기다릴 뿐). `send()` 자체의 타임아웃은 그 한 번의 왕복(대개 텔레그램 서버와의
네트워크 호출)만 커버하면 된다.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading

from leetkit_manager.process_runner import child_env

_DEFAULT_STEP_TIMEOUT = 30.0
# 창 없는 exe에서 자식을 띄우면 빈 콘솔 창이 뜬다 — process_runner와 같은 이유로 막는다.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


class InteractiveProcess:
    def __init__(self, cmd: list[str]):
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            # errors 없이 strict로 두면, 자식이 UTF-8이 아닌 바이트를 한 번만 흘려도
            # _read_loop의 광범위 except가 UnicodeDecodeError를 삼켜 EOF로 위장한다
            # (구버전 Lens가 stdout 인코딩을 재설정하지 않는 경우 실제로 발생) —
            # run_cli과 동일하게 replace로 맞춰 최소한 줄은 읽히게 한다.
            errors="replace",
            bufsize=1,  # 줄 단위 버퍼링 — 한 줄 쓸 때마다 상대가 바로 읽을 수 있게
            creationflags=_NO_WINDOW,
            env=child_env(),
        )
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        # Windows 파이프는 select()가 안 먹으므로(POSIX 전용) 별도 스레드 + 큐로 우회.
        try:
            for line in self._proc.stdout:
                self._queue.put(line)
        except Exception:
            pass
        finally:
            self._queue.put(None)  # EOF/스트림 종료 신호

    def _drain_stale(self) -> bool:
        """새 입력을 보내기 직전에 큐에 남아있는 묵은 줄을 버린다.
        반환값: 스트림이 이미 끝났으면(EOF 표시를 봤으면) False.

        자식이 입력 하나에 상태 줄을 두 개 이상 뱉는 경로가 있다(예: 잘못된 전화번호 →
        `error` + 같은 단계 프롬프트 재emit). send()는 한 줄만 소비하므로 그 여분이
        큐에 남고, 다음 send()가 그 묵은 줄을 "이번 응답"으로 착각해 이후 모든 왕복이
        한 칸씩 밀린다 — 실제 subprocess로 재현해 확인한 버그다. 이미 배포된 구버전
        TelegramLens도 그 동작을 하므로, 자식을 고치는 것과 별개로 부모가 스스로
        방어한다. 새 입력을 보내는 시점에 큐에 남은 줄은 정의상 UI가 이미 지나간
        이전 교환의 잔여물이라 버리는 게 맞다."""
        while True:
            try:
                line = self._queue.get_nowait()
            except queue.Empty:
                return True
            if line is None:
                return False

    def send(self, payload: dict, *, timeout: float = _DEFAULT_STEP_TIMEOUT) -> dict | None:
        """한 줄 보내고 다음 상태 한 줄을 기다린다. timeout 안에 응답이 없거나
        스트림이 끝났으면(자식이 죽었거나 EOF) None."""
        if not self._drain_stale():
            return None
        try:
            # ensure_ascii=True — 비ASCII(한글 2단계 인증 비밀번호 등)를 \uXXXX로
            # 이스케이프해서 순수 ASCII만 파이프에 흘린다. 자식이 stdin 인코딩을
            # UTF-8로 재설정하지 않은 버전이어도 깨지지 않는다.
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
            self._proc.stdin.flush()
        except Exception:
            return None

        try:
            line = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def read_first(self, *, timeout: float = _DEFAULT_STEP_TIMEOUT) -> dict | None:
        """아무것도 보내지 않고 자식이 먼저 emit하는 첫 상태 줄을 기다린다(세션 시작 시)."""
        try:
            line = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def close(self) -> None:
        """자식 프로세스 정리 — 모달을 취소하거나 창을 닫을 때 고아 프로세스가
        안 남게 반드시 호출한다."""
        try:
            self._proc.terminate()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass

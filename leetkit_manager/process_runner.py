"""Subprocess 실행 계층 — timeout, stdout/stderr 분리, JSON 파싱, stdin 입력.

Manager가 각 Lens CLI를 subprocess로 부르는 유일한 경로. 여기서 지키는 두 가지 불변식:

1. 모든 호출은 기본 30초 timeout을 갖는다(공통 수용 기준: 한 Lens doctor가 hang돼도
   30초 후 중단하고 나머지 Lens 진단은 계속되어야 한다).
2. `activate --stdin`에 넘기는 라이선스 키 원문은 커맨드라인 인자로도, 이 모듈이 남기는
   어떤 기록에도 나타나지 않는다 — stdin 파이프로만 전달한다. 이 모듈은 애초에 아무것도
   로그/파일에 쓰지 않으므로(순수 함수), 호출자가 ProcessResult를 그대로 로깅하지 않는 한
   원문이 새어나갈 경로가 없다.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

DEFAULT_TIMEOUT = 30.0


@dataclass
class ProcessResult:
    cmd: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    # "not_found"(커맨드 자체가 없음) | "timeout" | None(정상 종료 — exit_code로 성공/실패 판단)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.exit_code == 0


def run_cli(
    cmd: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    input_text: str | None = None,
) -> ProcessResult:
    """cmd를 실행한다. input_text가 있으면 stdin으로만 전달한다(로그에 남기지 않는다).

    취소: 호출자가 별도 스레드/프로세스에서 이 함수를 돌리고 있다면, timeout이 지나면
    이 함수가 자체적으로 자식 프로세스를 죽이고 반환하므로 별도 취소 신호가 필요 없다.
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout if isinstance(e.stdout, str) else ""
        stderr = e.stderr if isinstance(e.stderr, str) else ""
        return ProcessResult(
            cmd=cmd,
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            duration_s=time.monotonic() - start,
            error="timeout",
        )
    except FileNotFoundError:
        return ProcessResult(
            cmd=cmd,
            exit_code=None,
            stdout="",
            stderr="",
            timed_out=False,
            duration_s=time.monotonic() - start,
            error="not_found",
        )

    return ProcessResult(
        cmd=cmd,
        exit_code=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        timed_out=False,
        duration_s=time.monotonic() - start,
    )


def run_json_cli(
    cmd: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    input_text: str | None = None,
) -> tuple[ProcessResult, dict | None]:
    """run_cli + stdout을 JSON 객체 하나로 파싱 시도.

    Lens CLI 계약(3.1)은 "--json 이면 stdout에 JSON 하나만 출력"을 요구하지만, 계약
    위반(옛 버전, 손상된 설치)에도 Manager 자체가 죽으면 안 되므로 파싱 실패는 예외 대신
    (result, None)으로 조용히 반환한다 — 호출자가 "호환되지 않는 Lens 버전"으로 표시한다.
    """
    result = run_cli(cmd, timeout=timeout, input_text=input_text)
    if result.error or not result.stdout.strip():
        return result, None
    try:
        return result, json.loads(result.stdout)
    except json.JSONDecodeError:
        return result, None

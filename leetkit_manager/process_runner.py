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
import sys
import time
from dataclasses import dataclass

DEFAULT_TIMEOUT = 30.0

# 창 없는(--windowed) exe에서 자식 프로세스를 띄우면 Windows가 그때마다 새 콘솔 창을
# 만든다 — 사용자 눈엔 "빈 검은 터미널"이 깜빡이거나, 설치처럼 오래 걸리는 명령에선
# 몇십 초 동안 덩그러니 떠 있는다(실사용 중 지적됨). 진단은 새로고침마다 Lens 3개를
# 부르므로 이게 계속 깜빡인다. 출력은 어차피 파이프로 받으니 창은 필요 없다.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


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
            creationflags=_NO_WINDOW,
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


def run_cli_streaming(
    cmd: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    on_line=None,
) -> ProcessResult:
    """run_cli와 같지만 출력을 한 줄씩 받는 즉시 `on_line(line)`으로 흘려준다.

    설치처럼 수십 초 걸리는 명령에서 "지금 뭘 하고 있는지"를 화면에 보여주기 위한 것 —
    끝날 때까지 아무 표시가 없으면 사용자는 멈춘 줄 안다(실사용 중 지적됨). uv는 진행
    상황을 stderr에 사람이 읽을 수 있는 형태로 흘리므로 stdout과 합쳐서 읽는다.
    반환값은 run_cli와 동일한 계약이라 호출부가 결과 판정을 똑같이 할 수 있다.
    """
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 진행 표시(stderr)와 결과(stdout)를 시간 순서대로
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError:
        return ProcessResult(
            cmd=cmd, exit_code=None, stdout="", stderr="", timed_out=False,
            duration_s=time.monotonic() - start, error="not_found",
        )

    collected: list[str] = []
    try:
        for line in proc.stdout:
            collected.append(line)
            if on_line:
                try:
                    on_line(line.rstrip())
                except Exception:
                    pass  # 진행 표시가 실패해도 설치 자체를 망치면 안 된다
            if time.monotonic() - start > timeout:
                proc.kill()
                return ProcessResult(
                    cmd=cmd, exit_code=None, stdout="".join(collected), stderr="",
                    timed_out=True, duration_s=time.monotonic() - start, error="timeout",
                )
        proc.wait(timeout=max(1.0, timeout - (time.monotonic() - start)))
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        return ProcessResult(
            cmd=cmd, exit_code=None, stdout="".join(collected), stderr=str(e),
            timed_out=False, duration_s=time.monotonic() - start, error="timeout",
        )

    return ProcessResult(
        cmd=cmd, exit_code=proc.returncode, stdout="".join(collected), stderr="",
        timed_out=False, duration_s=time.monotonic() - start,
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

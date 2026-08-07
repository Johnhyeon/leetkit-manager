"""LeetKit Manager CLI 진입점.

    leetkit-manager                        데스크톱 대시보드(pywebview) 실행 — 기본 동작
    leetkit-manager gui                    위와 동일(명시적으로)
    leetkit-manager diagnose [--online]   전체 진단 대시보드(텍스트)
    leetkit-manager check-updates          세 Lens 최신 버전 조회

인자 없이 실행(바탕화면 바로가기 더블클릭 포함)하면 gui로 간다 — 설치 후에는 터미널
명령을 몰라도 아이콘만 누르면 되게 하기 위함(uv tool install이 만드는 leetkit-manager.exe
자체가 이미 실행 파일이라 별도 빌드 없이 바로가기만 놓으면 된다. shortcut.py 참고).
"""

from __future__ import annotations

import argparse
import sys

from leetkit_manager import orchestrator
from leetkit_manager.lens_contract import LENSES


def _cmd_diagnose(args: argparse.Namespace) -> int:
    diagnoses = orchestrator.run_full_diagnosis(online=args.online)
    summary = orchestrator.summarize(diagnoses)

    print(f"{summary['total']}개 중 {summary['ok']}개 정상", end="")
    if summary["update_available"]:
        print(f" · 업데이트 {summary['update_available']}개", end="")
    if summary["action_needed"]:
        print(f" · 조치 필요 {summary['action_needed']}개", end="")
    print()
    print()

    for d in diagnoses:
        version = d.report.installed_version if d.report else "?"
        print(f"[{d.readiness}] {d.lens.display_name} (v{version})")
        if d.report:
            for c in d.report.checks:
                if c.status in ("ok", "skip", "info-skip"):
                    continue
                label = "진행중" if c.status == "active" else "문제"
                print(f"    - [{label}] {c.id}: {c.summary}")
                if c.action:
                    print(f"      action: {c.action}")
        print()

    any_action_needed = summary["action_needed"] > 0
    return 1 if any_action_needed else 0


def _cmd_check_updates(args: argparse.Namespace) -> int:
    latest = orchestrator.check_for_updates()
    for lens in LENSES:
        v = latest.get(lens.name)
        print(f"{lens.display_name}: {v if v else '확인 실패'}")
    return 0


def _wait_for_pid_exit(pid: int, timeout_s: float = 30.0) -> None:
    """그 PID가 사라질 때까지(최대 timeout_s) 기다린다.

    자체 업데이트가 새 버전을 띄울 때만 쓴다. 옛 프로세스가 아직 살아 있는 동안 새
    프로세스가 뜨면 중복 실행 방지에 걸려 스스로 종료하고, 곧이어 옛 프로세스도
    닫히면서 아무것도 안 남는다 — 화면엔 "다시 시작합니다"만 뜨고 실제로는 아무 일도
    안 일어난 것처럼 보였다. 시간이 다 되면 그냥 진행한다(영영 안 뜨는 것보다는
    창이 두 개 뜨는 편이 낫다 — single_instance의 판단 기준과 같은 정신)."""
    import time

    import psutil

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return
        time.sleep(0.2)


def _cmd_selftest(args: argparse.Namespace) -> int:
    """창을 띄우기 직전까지 필요한 것들이 실제로 불러와지는지만 확인하고 끝낸다.

    빌드된 exe가 "켜자마자 죽는" 사고를 릴리스 전에 잡으려고 둔다. 실제로 v0.1.13
    exe가 그랬다 — 번들 안에서 cffi(파이썬 쪽)와 _cffi_backend(컴파일된 쪽) 버전이
    어긋나 webview가 .NET 런타임을 못 만들고 그 자리에서 죽었다. 빌드는 성공했고
    `--help`도 멀쩡했기 때문에 아무 검사에도 안 걸렸다.

    무거운 초기화(창 생성)까지 가지 않고 import만 한다 — CI에 화면이 없어도 돈다.

    결과는 `--report <경로>`로 파일에 쓴다. --windowed로 빌드한 exe는 stdout·stderr가
    아예 없어서, 실패해도 종료 코드만 남고 왜 실패했는지는 어디에도 안 남는다 —
    실제로 CI에서 그렇게 한 번 막혔다. 파일로 받아야 원인을 볼 수 있다.
    """
    import traceback

    report = getattr(args, "report", None)

    def _write(text: str) -> None:
        if sys.stdout:
            print(text)
        if report:
            try:
                with open(report, "w", encoding="utf-8") as f:
                    f.write(text)
            except OSError:
                pass

    try:
        import webview  # noqa: F401

        if sys.platform == "win32":
            # 여기가 v0.1.13에서 터진 자리다(pythonnet → clr_loader → cffi).
            import webview.platforms.winforms  # noqa: F401
    except BaseException:  # noqa: BLE001 — SystemExit로 죽는 경우까지 원인을 남겨야 한다
        _write("selftest FAILED\n" + traceback.format_exc())
        return 1
    _write("selftest OK")
    return 0


def _cmd_gui(args: argparse.Namespace) -> int:
    from leetkit_manager.ui.app import run

    wait_for = getattr(args, "wait_for_exit", None)
    if wait_for:
        _wait_for_pid_exit(wait_for)
    run()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="leetkit-manager")
    # 서브커맨드 없이 바로가기로 뜨는 경우가 기본이라 최상위에도 둔다(자체 업데이트가
    # 새 버전을 띄울 때 `<exe> --wait-for-exit <pid>` 형태로 붙인다).
    p.add_argument("--wait-for-exit", type=int, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command")

    diagnose = sub.add_parser("diagnose", help="전체 Lens 진단 대시보드(텍스트)")
    diagnose.add_argument("--online", action="store_true", help="실제 외부 연결 검사 포함")
    diagnose.set_defaults(func=_cmd_diagnose)

    check_updates = sub.add_parser("check-updates", help="세 Lens 최신 버전 조회(PyPI)")
    check_updates.set_defaults(func=_cmd_check_updates)

    selftest = sub.add_parser("selftest", help=argparse.SUPPRESS)
    selftest.add_argument("--report", help=argparse.SUPPRESS)
    selftest.set_defaults(func=_cmd_selftest)

    gui = sub.add_parser("gui", help="데스크톱 대시보드(pywebview) 실행")
    gui.add_argument("--wait-for-exit", type=int, help=argparse.SUPPRESS)
    gui.set_defaults(func=_cmd_gui)

    return p


def main() -> None:
    args = _build_parser().parse_args()
    # 서브커맨드 없이 실행(바로가기 더블클릭 등)하면 gui가 기본 동작.
    func = getattr(args, "func", _cmd_gui)
    sys.exit(func(args))


if __name__ == "__main__":
    main()

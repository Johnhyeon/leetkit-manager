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

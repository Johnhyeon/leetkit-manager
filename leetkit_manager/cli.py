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


def _cmd_gui(args: argparse.Namespace) -> int:
    from leetkit_manager.ui.app import run

    run()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="leetkit-manager")
    sub = p.add_subparsers(dest="command")

    diagnose = sub.add_parser("diagnose", help="전체 Lens 진단 대시보드(텍스트)")
    diagnose.add_argument("--online", action="store_true", help="실제 외부 연결 검사 포함")
    diagnose.set_defaults(func=_cmd_diagnose)

    check_updates = sub.add_parser("check-updates", help="세 Lens 최신 버전 조회(PyPI)")
    check_updates.set_defaults(func=_cmd_check_updates)

    gui = sub.add_parser("gui", help="데스크톱 대시보드(pywebview) 실행")
    gui.set_defaults(func=_cmd_gui)

    return p


def main() -> None:
    args = _build_parser().parse_args()
    # 서브커맨드 없이 실행(바로가기 더블클릭 등)하면 gui가 기본 동작.
    func = getattr(args, "func", _cmd_gui)
    sys.exit(func(args))


if __name__ == "__main__":
    main()

"""pywebview JS-Python 브릿지. JS에서 `window.pywebview.api.<method>(...)`로 호출된다.

여기 함수들은 orchestrator를 부르고 그 결과를 JSON 직렬화 가능한 dict/list/기본형으로
변환만 한다 — Lens 로직(어떤 명령을 어떻게 부를지)은 여기 없다. 라이선스 키는 JS →
Python 인메모리 호출로만 전달되고(subprocess 인자·로그를 거치지 않음) 여기서 그대로
`orchestrator.activate_lens`의 stdin 경로로 넘어간다.
"""

from __future__ import annotations

import webbrowser

from leetkit_manager import orchestrator, package_service, redaction
from leetkit_manager.lens_contract import LENSES, get_lens
from leetkit_manager.models import CheckResult
from leetkit_manager.orchestrator import LensDiagnosis

PATCH_NOTES_URL = "https://app.notion.com/p/LeetKit-3b48f7db5c9680639f35fd2655a47c58"


def _check_to_dict(c: CheckResult) -> dict:
    return {
        "id": c.id,
        "status": c.status,
        "summary": c.summary,
        "details": c.details,
        "repairable": c.repairable,
        "repair_id": c.repair_id,
        "action": c.action,
    }


def _diagnosis_to_dict(d: LensDiagnosis) -> dict:
    report = d.report
    checks = report.checks if report else []
    repairable = next((c for c in checks if c.repairable), None)
    return {
        "name": d.lens.name,
        "display_name": d.lens.display_name,
        "readiness": d.readiness,
        "not_installed": d.not_installed,
        "incompatible": d.incompatible,
        "installed_version": report.installed_version if report else None,
        "latest_version": report.latest_version if report else None,
        "update_available": report.update_available if report else None,
        "license_status": report.license.status if report else None,
        "license_id_masked": report.license.license_id_masked if report else None,
        "targets": report.targets if report else [],
        "checked_at": report.checked_at if report else None,
        "overall": report.overall if report else None,
        "checks": [_check_to_dict(c) for c in checks],
        "repairable_repair_id": repairable.repair_id if repairable else None,
    }


class Api:
    def diagnose(self, online: bool = False) -> dict:
        """전체 진단(2.4) — Lens별 순차 호출은 orchestrator가 이미 보장한다."""
        diagnoses = orchestrator.run_full_diagnosis(online=online)
        return {
            "summary": orchestrator.summarize(diagnoses),
            "lenses": [_diagnosis_to_dict(d) for d in diagnoses],
        }

    def diagnose_one(self, lens_name: str, online: bool = False) -> dict:
        lens = get_lens(lens_name)
        return _diagnosis_to_dict(orchestrator.diagnose_lens(lens, online=online))

    def register(self, lens_name: str) -> dict:
        """MCP 등록(setup) — Claude Desktop/Code 둘 다 대상으로."""
        lens = get_lens(lens_name)
        result = orchestrator.setup_lens(lens, ["claude-desktop", "claude-code"])
        return {"ok": result.ok, "error": result.error}

    def activate(self, lens_name: str, license_key: str) -> dict:
        lens = get_lens(lens_name)
        result = orchestrator.activate_lens(lens, license_key)
        return {
            "ok": result.ok,
            "license_id_masked": result.license_id_masked,
            "message": result.message,
            "error_code": result.error_code,
        }

    def repair(self, lens_name: str, repair_id: str) -> dict:
        lens = get_lens(lens_name)
        try:
            payload = orchestrator.repair_lens(lens, repair_id)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "result": payload}

    def install_or_update(self, lens_name: str) -> dict:
        """미설치면 최신 버전 설치, 이미 설치돼 있으면 최신으로 업데이트 — 둘 다
        `orchestrator.update_lens`(uv tool install --force) 하나로 처리된다."""
        lens = get_lens(lens_name)
        diag = orchestrator.diagnose_lens(lens)
        latest = (diag.report.latest_version if diag.report else None) or package_service.latest_pypi_version(
            lens.package_name
        )
        if not latest:
            return {"ok": False, "error": "최신 버전을 확인할 수 없습니다(네트워크를 확인하세요)."}
        previous = diag.report.installed_version if diag.report else None
        result = orchestrator.update_lens(lens, latest, previous_version=previous)
        return {"ok": result.ok, "rollback_command": result.rollback_command, "version": latest}

    def lens_names(self) -> list[str]:
        return [lens.name for lens in LENSES]

    def diagnostic_text(self, lens_name: str) -> str:
        """진단 결과를 사람이 읽고 붙여넣을 수 있는 텍스트로 — 문의·지원 요청용.

        키·전화번호·홈 디렉터리 사용자명 등은 이미 doctor JSON 자체가 원문을 담지
        않지만(1차 방어선), 복사해서 밖으로 나가는 텍스트이므로 redaction을 한 번 더
        거친다(2.4의 "진단 결과 복사" 마스킹 요구사항).
        """
        lens = get_lens(lens_name)
        diag = orchestrator.diagnose_lens(lens)
        report = diag.report

        lines = [f"[{lens.display_name}] v{report.installed_version if report else '?'} — {diag.readiness}"]
        if report:
            lines.append(f"최근 진단: {report.checked_at}")
            problems = [c for c in report.checks if c.status not in ("ok", "active", "skip", "info-skip")]
            in_progress = [c for c in report.checks if c.status == "active"]
            if not problems:
                lines.append("문제 없음")
            for c in problems:
                lines.append(f"- [{c.id}] {c.summary}")
                if c.action:
                    lines.append(f"  조치: {c.action}")
            for c in in_progress:
                lines.append(f"- [진행중][{c.id}] {c.summary}")
        return redaction.redact("\n".join(lines))

    def check_self_update(self) -> dict:
        """LeetKit Manager 자기 자신의 업데이트 확인 — Lens와 동일하게 PyPI 조회로."""
        from leetkit_manager import __version__ as current_version

        latest = package_service.latest_pypi_version("leetkit-manager")
        update_available = bool(latest) and package_service.version_gt(latest, current_version)
        return {"current": current_version, "latest": latest, "update_available": update_available}

    def self_update(self) -> dict:
        """`uv tool install --force leetkit-manager==<latest>`. 설치 후에는 앱을 다시
        시작해야 반영된다(Python은 실행 중 자기 코드를 다시 읽지 않으므로) — 재시작
        자체는 호출자(JS)가 안내하고 창을 닫는다."""
        latest = package_service.latest_pypi_version("leetkit-manager")
        if not latest:
            return {"ok": False, "error": "최신 버전을 확인할 수 없습니다(네트워크를 확인하세요)."}
        result = package_service.install_version("leetkit-manager", latest)
        return {"ok": result.ok, "version": latest}

    def create_support_bundle(self) -> dict:
        """지원 문의용 zip 생성(로그·상태 파일 안전 목록만) + 탐색기로 폴더 열기 +
        고객이 어떤 메일 앱에든 붙여넣을 받는사람/제목/본문 반환."""
        from leetkit_manager import support_bundle

        zip_path = support_bundle.create_bundle()
        support_bundle.reveal_in_file_manager(zip_path)
        return support_bundle.mail_compose_info(zip_path)

    def open_patch_notes(self) -> None:
        """패치노트(Notion) — 앱 안에 끼워 넣기엔 너무 크니 시스템 기본 브라우저로."""
        webbrowser.open(PATCH_NOTES_URL)

    def quit(self) -> None:
        """자기 업데이트 설치 후 창을 닫는다 — 반영되려면 재시작이 필요하기 때문
        (Python은 실행 중 자기 코드를 다시 읽지 않는다). 재실행은 바탕화면 바로가기로."""
        import webview

        if webview.windows:
            webview.windows[0].destroy()

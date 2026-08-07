"""pywebview JS-Python 브릿지. JS에서 `window.pywebview.api.<method>(...)`로 호출된다.

여기 함수들은 orchestrator를 부르고 그 결과를 JSON 직렬화 가능한 dict/list/기본형으로
변환만 한다 — Lens 로직(어떤 명령을 어떻게 부를지)은 여기 없다. 라이선스 키는 JS →
Python 인메모리 호출로만 전달되고(subprocess 인자·로그를 거치지 않음) 여기서 그대로
`orchestrator.activate_lens`의 stdin 경로로 넘어간다.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

from leetkit_manager import orchestrator, package_service, redaction
from leetkit_manager.lens_contract import LENSES, get_lens
from leetkit_manager.models import CheckResult
from leetkit_manager.orchestrator import LensDiagnosis

PATCH_NOTES_URL = "https://app.notion.com/p/LeetKit-3b48f7db5c9680639f35fd2655a47c58"
RELEASES_PAGE_URL = "https://github.com/Johnhyeon/leetkit-manager/releases/latest"
TELEGRAM_API_SIGNUP_URL = "https://my.telegram.org"
DART_API_SIGNUP_URL = "https://opendart.fss.or.kr"

# MCP 등록 대상 앱이 아직 없는 사용자를 위한 받는 곳. Lens는 이 앱들 위에서만 동작하므로,
# 없는 사람에게는 "등록"보다 "먼저 받기"를 안내해야 한다.
CLAUDE_DESKTOP_DOWNLOAD_URL = "https://claude.ai/download"
CLAUDE_CODE_DOWNLOAD_URL = "https://claude.ai/code"
CODEX_DOWNLOAD_URL = "https://developers.openai.com/codex/cli/"

# 라이선스 키를 넣으려다 "아직 없다"는 걸 깨닫는 자리에서 살 곳을 알려준다. CLI로
# 활성화하면 이미 이 주소를 안내하는데(각 Lens licensing.py의 PURCHASE_URL) Manager
# 모달에는 없어서, 같은 제품인데 경로에 따라 안내가 달랐다. 세 Lens가 같은 LP를 쓴다.
PURCHASE_URL = "https://litt.ly/leetkey_lab/sale/hzGHnRY"

_ALLOWED_EXTERNAL_URLS = frozenset(
    {CLAUDE_DESKTOP_DOWNLOAD_URL, CLAUDE_CODE_DOWNLOAD_URL, CODEX_DOWNLOAD_URL, PURCHASE_URL}
)


def _check_to_dict(c: CheckResult) -> dict:
    return {
        "id": c.id,
        "status": c.status,
        "summary": c.summary,
        "details": c.details,
        "repairable": c.repairable,
        "repair_id": c.repair_id,
        "action": c.action,
        "critical": c.critical,
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
        "extra_credentials": list(d.lens.extra_credentials),
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
        "problem_detail": _problem_detail(d),
    }


def _is_claude_blocking(result) -> bool:
    """설치·삭제가 "파일 사용 중"으로 실패했고 실제로 Claude Desktop이 떠 있는지.
    이 조합일 때만 UI가 "Claude를 닫고 다시 시도" 버튼을 띄운다 — Claude가 꺼져 있는데
    그 안내를 하면 엉뚱한 곳을 헤매게 된다."""
    if result is None or result.ok:
        return False
    return package_service.looks_like_file_in_use(result) and package_service.is_claude_desktop_running()


def _first_meaningful_line(text: str | None) -> str | None:
    """구분선(`====`)·빈 줄을 건너뛰고 실제 내용이 있는 첫 줄. 옛 버전 doctor는
    맨 위에 장식용 구분선부터 찍어서, 그냥 첫 줄을 보여주면 아무 정보가 안 된다."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped and any(ch.isalnum() for ch in stripped):
            return stripped[:200]
    return None


def _problem_detail(d: LensDiagnosis) -> str | None:
    """"호환되지 않는 Lens 버전"처럼 원인을 삼키는 상태에 실제 근거를 붙인다.

    이 판정은 doctor를 부르긴 했는데 JSON을 못 읽었다는 뜻이고, 원인은 제각각이다
    (옛 버전이라 --json을 모름 / 실행은 됐지만 즉시 죽음 / 다른 프로그램이 같은
    이름으로 PATH에 있음). 라벨만 보여주면 사용자도 나도 원인을 알 수 없어서
    "업데이트해도 그대로"에 갇힌다 — 실제 종료 코드와 출력 첫 줄을 그대로 보여준다.
    """
    if not d.incompatible:
        return None
    p = d.process
    parts = [f"진단 명령을 실행했지만 결과를 읽지 못했습니다 (종료 코드 {p.exit_code})."]
    snippet = _first_meaningful_line(p.stdout) or _first_meaningful_line(p.stderr)
    if snippet:
        parts.append(f"받은 응답: {snippet}")
    if p.error == "timeout":
        parts.append("응답이 제한 시간 안에 오지 않았습니다.")
    parts.append("대부분 옛 버전이 남아 있는 경우입니다 — '업데이트'가 안 되면 '삭제' 후 다시 설치해보세요.")
    return redaction.redact(" ".join(parts))


def _install_failure_reason(process) -> str | None:
    """설치/업데이트가 왜 실패했는지 한 줄로. 알 수 없으면 None.

    uv는 실패 이유를 stderr에 꽤 또렷하게 적는다(파일 사용 중, 버전 없음, 네트워크 등) —
    그걸 그대로 보여주는 게 "실패했습니다"보다 훨씬 낫다. 시간 초과는 stderr가 비어
    있으므로 따로 문구를 만든다."""
    if process is None:
        return None
    if getattr(process, "timed_out", False):
        return "시간이 오래 걸려 중단했습니다. 네트워크가 느리거나 받을 파일이 많을 때 그렇습니다 — 다시 시도해보세요."
    detail = _first_meaningful_line(getattr(process, "stderr", "")) or _first_meaningful_line(
        getattr(process, "stdout", "")
    )
    if detail and _looks_like_version_not_on_index(getattr(process, "stderr", "") or detail):
        # 안전망 — 최신 버전 판단을 simple 인덱스 기준으로 바꿔서(latest_pypi_version)
        # 이 상황은 거의 안 생기지만, 다른 경로로라도 걸리면 uv의 원문("no solution
        # found")만 보여주는 건 도움이 안 된다. 기다리면 풀린다는 걸 알려준다.
        return "방금 올라온 버전이라 아직 배포처에 반영되지 않았습니다 — 몇 분 뒤 다시 시도해주세요."
    return redaction.redact(detail) if detail else None


def _looks_like_version_not_on_index(text: str) -> bool:
    """uv가 "그 버전은 없다"고 말한 건지. 문구가 바뀔 수 있어 여러 표현을 본다."""
    lowered = (text or "").lower()
    return "no solution found" in lowered or "no version" in lowered or "not found in the package registry" in lowered


class Api:
    def __init__(self) -> None:
        # 후기 링크는 JS가 건네주는 게 아니라 Python이 원격 설정에서 받아 여기 들고
        # 있는다 — 프론트엔드가 임의의 주소를 열게 만드는 통로가 생기지 않게.
        self._review_url: str | None = None

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

    def register(self, lens_name: str, targets: list[str] | None = None) -> dict:
        """MCP 등록(setup). targets 생략 시 기존과 동일하게 Claude Desktop/Code 둘 다."""
        lens = get_lens(lens_name)
        result = orchestrator.setup_lens(lens, targets or ["claude-desktop", "claude-code"])
        return {"ok": result.ok, "error": result.error}

    def available_targets(self, lens_name: str) -> list[dict]:
        """MCP 등록 대상 선택 모달용 — 각 타겟의 id/라벨/설치 여부/설치 안내 링크.

        예전엔 claude-desktop/claude-code를 설치 여부와 무관하게 항상 `installed: True`로
        내려줬다 — 앱이 없는 사람도 체크하고 등록할 수 있었고, 등록은 "성공"하지만
        그 설정 파일을 읽어갈 앱이 없어서 아무 일도 일어나지 않았다. 실제 설치 여부를
        확인하고, 없으면 UI가 받는 곳 링크를 같이 보여줄 수 있게 install_url을 준다."""
        get_lens(lens_name)  # 존재 확인(모르는 lens_name이면 여기서 ValueError)
        return [
            {
                "id": "claude-desktop", "label": "Claude Desktop",
                "installed": package_service.is_claude_desktop_installed(),
                "install_url": CLAUDE_DESKTOP_DOWNLOAD_URL,
            },
            {
                "id": "claude-code", "label": "Claude Code",
                "installed": package_service.is_claude_code_installed(),
                "install_url": CLAUDE_CODE_DOWNLOAD_URL,
            },
            {
                "id": "codex", "label": "Codex CLI",
                "installed": package_service.is_codex_installed(),
                "install_url": CODEX_DOWNLOAD_URL,
            },
        ]

    def open_url(self, url: str) -> bool:
        """설치 안내 페이지 열기 — 위 available_targets가 준 install_url만 허용한다
        (임의 URL을 열어주는 통로가 되지 않게)."""
        if url not in _ALLOWED_EXTERNAL_URLS:
            return False
        webbrowser.open(url)
        return True

    def activate(self, lens_name: str, license_key: str) -> dict:
        lens = get_lens(lens_name)
        result = orchestrator.activate_lens(lens, license_key)
        return {
            "ok": result.ok,
            "license_id_masked": result.license_id_masked,
            "message": result.message,
            "error_code": result.error_code,
        }

    def register_api_key(self, lens_name: str, credential_kind: str, api_key: str) -> dict:
        """라이선스 키 말고 추가로 필요한 자격증명(DartLens의 DART API 키 등) 등록."""
        lens = get_lens(lens_name)
        try:
            result = orchestrator.register_api_key(lens, credential_kind, api_key)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": result.ok,
            "error": result.error,
            "error_code": result.error_code,
            "key_tail_masked": result.raw.get("key_tail_masked"),
        }

    def open_dart_api_signup(self) -> None:
        """opendart.fss.or.kr — DartLens를 처음 쓰는 사람이 DART OpenAPI 키를 발급받는
        곳(DartLens 자신의 setup_claude.py/_safe.py가 텍스트로만 안내하던 것과 같은
        URL). GUI에서는 링크 텍스트가 아니라 실제로 열어준다."""
        webbrowser.open(DART_API_SIGNUP_URL)

    def open_telegram_api_signup(self) -> None:
        """my.telegram.org — TelegramLens를 처음 쓰는 사람이 API_ID/API_HASH를 발급받는
        곳. 기존 대화형 CLI(login_cli.py)가 이 단계에서 자동으로 브라우저를 열어주던 것과
        동일하게, GUI 마법사의 need_credentials 단계에서도 자동으로 열어준다."""
        webbrowser.open(TELEGRAM_API_SIGNUP_URL)

    def telegram_login_start(self) -> dict:
        """텔레그램 로그인 마법사 시작 — TelegramLens 전용(전화번호 → SMS 코드 → 필요하면
        2단계 인증까지 여러 번 대화하는 유일한 흐름이라 activate/register처럼 한 번에
        끝나지 않는다)."""
        return orchestrator.start_telegram_login()

    def telegram_login_step(self, payload: dict) -> dict:
        return orchestrator.send_telegram_login_step(payload)

    def telegram_login_cancel(self) -> None:
        orchestrator.cancel_telegram_login()

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
        return {
            "ok": result.ok,
            "rollback_command": result.rollback_command,
            "version": latest,
            "claude_blocking": _is_claude_blocking(result.install),
            # 실패 이유를 같이 돌려준다. 예전엔 ok=False만 보내서 화면에 "실패했습니다"
            # 밖에 못 띄웠고, 사용자도 우리도 원인을 알 방법이 없었다(실제로 맥에서
            # 업데이트가 계속 실패했는데 아무 단서가 없었다).
            "error": None if result.ok else _install_failure_reason(result.install),
        }

    def uninstall(self, lens_name: str) -> dict:
        """`uv tool uninstall` + PATH에 남은 옛 pip 설치 잔재까지 함께 정리 — 재설치가
        필요한 경우(예: "호환되지 않는 Lens 버전"이 업데이트로도 안 풀리는 경우)를
        위해 카드에서 바로 지울 수 있게 한다. 자세한 이유는
        orchestrator.uninstall_lens() 참고. 라이선스/API 키 등 자격증명은 그대로 남아
        재설치 후 다시 입력할 필요가 없다."""
        lens = get_lens(lens_name)
        result = orchestrator.uninstall_lens(lens)
        blocking = _is_claude_blocking(result.uninstall)
        error = None
        if not result.ok:
            error = (
                "Claude Desktop이 이 파일을 사용 중이라 지울 수 없습니다."
                if blocking
                else (redaction.redact(result.uninstall.stderr) or "삭제에 실패했습니다.")
            )
        return {"ok": result.ok, "error": error, "claude_blocking": blocking}

    def install_progress(self) -> str | None:
        """설치 중 화면에 보여줄 현재 단계. UI가 짧은 주기로 읽어간다 —
        수십 초 동안 아무 변화가 없으면 사용자는 멈춘 줄 알기 때문."""
        return package_service.current_install_progress()

    def claude_desktop_running(self) -> bool:
        """MCP 등록·설치를 끝낸 뒤 "Claude Desktop을 다시 켜야 한다"고 안내할지 판단용.
        자세한 이유는 package_service.is_claude_desktop_running() 참고."""
        return package_service.is_claude_desktop_running()

    def quit_claude_desktop(self) -> dict:
        """설치·삭제가 "파일 사용 중"으로 막혔을 때 Claude Desktop만 먼저 닫는다
        (경로로 판별하므로 Claude Code CLI 세션은 건드리지 않는다)."""
        if package_service.quit_claude_desktop():
            return {"ok": True, "error": None}
        return {"ok": False, "error": "Claude Desktop을 종료하지 못했습니다. 직접 종료한 뒤 다시 시도해주세요."}

    def launch_claude_desktop(self) -> dict:
        """위에서 닫은 Claude Desktop을 도로 켠다."""
        return {"ok": package_service.launch_claude_desktop(), "error": None}

    def restart_claude_desktop(self) -> dict:
        """Claude Desktop을 껐다 켠다 — MCP 등록을 반영하는 마지막 단계.

        "트레이 아이콘 우클릭 → 종료 → 다시 실행"은 40-50대 사용자에게 실제로 막히는
        구간이라(창을 닫아도 트레이에 남는 걸 모르는 경우가 대부분) 버튼 하나로 대신한다.
        경로로 Claude Desktop만 골라 종료하므로 Claude Code CLI 작업은 영향받지 않는다."""
        return package_service.restart_claude_desktop()

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
        """LeetKit Manager 자기 자신의 업데이트 확인.

        `uv tool install`로 깔린 버전은 Lens와 동일하게 PyPI 조회. 단일 exe로 받은
        버전은 uv가 전혀 관여하지 않는 실행 파일 자체라 PyPI가 아니라 GitHub Release를
        본다(거기 올라가는 LeetKitManager.exe가 그 사람이 실제로 받은 물건이므로).
        """
        from leetkit_manager import __version__ as current_version

        if package_service.is_frozen_exe():
            release = package_service.latest_github_release()
            latest = release.get("version") if release else None
        else:
            latest = package_service.latest_pypi_version("leetkit-manager")
        update_available = bool(latest) and package_service.version_gt(latest, current_version)
        return {"current": current_version, "latest": latest, "update_available": update_available}

    def self_update(self) -> dict:
        """`uv tool install` 버전: `uv tool install --force leetkit-manager==<latest>`.
        단일 exe 버전: GitHub Release에서 새 exe를 받아 지금 실행 중인 파일 자체를
        바꿔치기하고 재실행(uv tool install이 아예 관여하지 않음 — 애초에 uv로 깐 게
        아니므로). 두 경우 다 반영되려면 지금 프로세스는 종료해야 해서, 호출자(JS)가
        재시작을 안내하고 창을 닫는다."""
        if package_service.is_frozen_exe():
            release = package_service.latest_github_release()
            if not release or not release.get("exe_url"):
                return {"ok": False, "error": "최신 버전을 확인할 수 없습니다(네트워크를 확인하세요)."}
            import tempfile
            from pathlib import Path

            # 고정 경로(%TEMP%\LeetKitManager.new.exe)는 다운로드와 교체 사이에 남이
            # 바꿔치기할 여지가 있다 — 매번 새 전용 폴더에 받는다.
            tmp_dir = Path(tempfile.mkdtemp(prefix="leetkit-update-"))
            tmp_path = tmp_dir / "LeetKitManager.new.exe"
            if not package_service.download_file(release["exe_url"], tmp_path):
                return {"ok": False, "error": "새 버전 다운로드에 실패했습니다(네트워크를 확인하세요)."}

            # 실행 중인 exe를 통째로 바꾸는 동작이라, 받은 파일이 릴리스에 올라간 그
            # 파일이 맞는지 확인하고 교체한다. 체크섬 자산이 없는 옛 릴리스는 검증을
            # 건너뛰되(하위 호환), 있는데 안 맞으면 절대 교체하지 않는다.
            expected = (
                package_service.fetch_expected_sha256(release["sha256_url"])
                if release.get("sha256_url")
                else None
            )
            if expected and package_service.sha256_of_file(tmp_path) != expected:
                return {
                    "ok": False,
                    "error": "내려받은 파일이 손상되었거나 위변조되었습니다. 업데이트를 중단했습니다.",
                }

            result = package_service.replace_running_exe(tmp_path)
            return {"ok": result.ok, "version": release["version"], "error": result.stderr if not result.ok else None}

        latest = package_service.latest_pypi_version("leetkit-manager")
        if not latest:
            return {"ok": False, "error": "최신 버전을 확인할 수 없습니다(네트워크를 확인하세요)."}
        result = package_service.install_version("leetkit-manager", latest)
        if not result.ok:
            return {"ok": False, "version": latest}
        # 예전엔 여기서 그냥 끝나서, "앱을 다시 시작합니다"라고 안내해놓고 닫히기만 했다
        # (윈도우·맥 공통 — 단일 exe 쪽만 replace_running_exe가 같이 띄우고 있었다).
        return {"ok": True, "version": latest, "relaunching": package_service.relaunch_after_exit()}

    def create_support_bundle(self) -> dict:
        """지원 문의용 zip 생성(로그·상태 파일 안전 목록만) + 탐색기로 폴더 열기 +
        고객이 어떤 메일 앱에든 붙여넣을 받는사람/제목/본문 반환."""
        from leetkit_manager import support_bundle

        zip_path = support_bundle.create_bundle()
        support_bundle.reveal_in_file_manager(zip_path)
        return support_bundle.mail_compose_info(zip_path)

    def open_purchase_page(self) -> bool:
        """구매 페이지 열기 — 라이선스 모달의 "아직 없으신가요?"에서만 부른다."""
        webbrowser.open(PURCHASE_URL)
        return True

    def review_prompt(self, ready: bool) -> dict | None:
        """지금 후기를 물어볼 때면 표시할 내용, 아니면 None.

        `ready`는 "설치가 실제로 끝났는가" — 아직 설치 중이거나 문제를 고치는 중인
        사람에게 후기를 달라고 하면 역효과라 JS가 진단 결과를 보고 넘겨준다.

        네트워크를 타므로(원격 설정) 실패할 수 있는데, 실패는 곧 "안 띄움"이다 —
        후기 요청은 없어도 제품이 돌아가는 기능이라 오류를 보여줄 이유가 없다."""
        from leetkit_manager import review_prompt

        try:
            config = review_prompt.fetch_config()
            pending = review_prompt.pending_prompt(config, ready=bool(ready))
        except Exception:
            return None
        if pending is None:
            self._review_url = None
            return None
        # 링크가 없을 수 있다 — 리틀리 후기란은 구매자마다 주소가 달라 앱이 열어줄
        # 방법이 없고, 그때는 안내만 하고 버튼은 안 만든다(JS가 has_url을 보고 판단).
        self._review_url = pending.pop("url") or None
        pending["has_url"] = self._review_url is not None
        review_prompt.mark_asked()
        return pending

    def open_review_url(self) -> bool:
        """방금 review_prompt가 받아둔 주소만 연다(JS가 주소를 넘기지 않는다).
        링크를 실제로 열었으면 목적을 이룬 것이므로 다시 묻지 않는다."""
        from leetkit_manager import review_prompt

        if not self._review_url:
            return False
        webbrowser.open(self._review_url)
        review_prompt.mark_done()
        return True

    def review_prompt_never_again(self) -> bool:
        """"이미 남겼어요" — 다시는 묻지 않는다."""
        from leetkit_manager import review_prompt

        review_prompt.mark_done()
        return True

    def choose_shortcut_location(self) -> dict:
        """바로가기 저장 위치를 사용자가 직접 고르게 한다(폴더 선택 다이얼로그).
        `webview.start()`가 실제로 창을 띄운 뒤에만 호출 가능(pywebview 제약) — 그래서
        app.py가 아니라 여기, JS가 pywebviewready 이후에 부르는 경로에 있다. 취소하면
        바탕화면에 기본 생성(건너뛰기 취급 — 첫 실행 흐름이 막히지 않게).

        온보딩 마법사는 시작할 때마다 이 메서드를 무조건 호출하므로("나중에"로 미뤘다가
        다음 실행에 다시 "시작"을 누르는 경우 등), 이미 한 번 물어봤으면 다이얼로그를
        다시 띄우지 않고 바로 넘어간다."""
        import webview

        from leetkit_manager import shortcut

        # "물어본 적 있다"는 표시만 보고 건너뛰면, 바로가기가 실제로는 없는데도 아무것도
        # 안 하고 성공이라고 답한다 — 사용자가 지웠거나 옛 바로가기를 정리한 경우가 그렇고,
        # 마법사 말고는 만들 진입점이 없어서 영영 다시 못 만든다(실제로 맥에서 겪었다).
        # 표시가 있어도 파일이 없으면 다시 만든다. 다만 위치는 이미 답한 질문이므로
        # 다이얼로그로 또 묻지 않고 그때 고른 폴더(없으면 바탕화면)에 그대로 만든다.
        if shortcut.has_shortcut_been_offered():
            existing = shortcut.existing_shortcut()
            if existing is not None:
                return {"ok": True, "path": str(existing)}
            target_dir = shortcut.recorded_shortcut_dir() or (Path.home() / "Desktop")
            link_path = shortcut.create_shortcut_at(target_dir)
            return {"ok": link_path is not None, "path": str(link_path) if link_path else None}

        default_dir = str(Path.home() / "Desktop")
        chosen = None
        if webview.windows:
            result = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER, directory=default_dir)
            if result:
                chosen = Path(result[0])

        target_dir = chosen or (Path.home() / "Desktop")
        link_path = shortcut.create_shortcut_at(target_dir)
        # 실패했는데도 "물어봤다"로 기록하면, 다음 실행에서 has_shortcut_been_offered()
        # 가드에 걸려 재시도 자체가 막힌다 — 실제로 이렇게 한 번 실패가 영구히 남는
        # 문제가 있었다. 성공했을 때만 다시 안 물어보게 하고, 실패하면 다음 실행에서
        # 다시 시도할 수 있게 둔다.
        if link_path is not None:
            # 고른 폴더까지 적어둔다 — 나중에 바로가기를 고쳐야 할 때(맥의 .app 전환
            # 같은) 바탕화면이 아닌 곳에 만든 사람도 찾아갈 수 있게.
            shortcut.mark_shortcut_offered(target_dir)
        return {"ok": link_path is not None, "path": str(link_path) if link_path else None}

    def open_patch_notes(self) -> None:
        """패치노트(Notion) — 앱 안에 끼워 넣기엔 너무 크니 시스템 기본 브라우저로."""
        webbrowser.open(PATCH_NOTES_URL)

    def copy_to_clipboard(self, text: str) -> bool:
        """OS 클립보드로 직접 복사. WebView2/WKWebView 안에서 `navigator.clipboard`는
        임베드된 컨텍스트라 권한이 막혀 있는 경우가 있어(포커스·권한 정책 등), 브라우저
        API 대신 OS 클립보드를 직접 쓴다 — 항상 동작하고 권한 프롬프트도 없다."""
        if sys.platform == "win32":
            return self._copy_windows(text)
        if sys.platform == "darwin":
            return self._copy_macos(text)
        return False

    @staticmethod
    def _copy_windows(text: str) -> bool:
        import win32clipboard

        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
            return True
        except Exception:
            return False
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    @staticmethod
    def _copy_macos(text: str) -> bool:
        import subprocess

        try:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            return True
        except Exception:
            return False

    def quit(self) -> None:
        """자기 업데이트 설치 후 창을 닫는다 — 반영되려면 재시작이 필요하기 때문
        (Python은 실행 중 자기 코드를 다시 읽지 않는다). 재실행은 바탕화면 바로가기로."""
        import webview

        if webview.windows:
            webview.windows[0].destroy()

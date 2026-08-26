"""설치·진단·업데이트 상태 머신 — UI가 직접 subprocess/uv/config 파일을 만지지 않도록
모든 Lens 조작을 이 모듈 하나로 모은다. `ui/`는 여기 함수들의 반환값을 표시만 한다.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from leetkit_manager import config_backup, package_service, redaction
from leetkit_manager.lens_contract import LENSES, LensSpec
from leetkit_manager.models import ActivateResult, DoctorReport, SetupResult
from leetkit_manager.process_runner import DEFAULT_TIMEOUT, ProcessResult, run_json_cli

# Manager가 이해하는 doctor JSON schema_version. 이 범위 밖(또는 파싱 자체가 안 되는)
# Lens는 "호환되지 않는 Lens 버전"으로 표시한다(공통 수용 기준).
SUPPORTED_SCHEMA_VERSIONS = (1,)

# 진단 시점의 PyPI 버전 확인 전용 타임아웃 — Lens 자신의 --online(실 데이터소스 연결
# 확인까지 포함)보다 훨씬 가벼운 조회 하나뿐이라, 오프라인이어도 대시보드가 이것 때문에
# 오래 멈춰 있지 않게 package_service의 기본 10초보다 짧게 잡는다(Lens 3개 순차 호출
# 기준 최악의 경우에도 대략 12초 안에 끝나도록).
_UPDATE_CHECK_TIMEOUT = 4.0


@dataclass
class LensDiagnosis:
    lens: LensSpec
    report: DoctorReport | None
    process: ProcessResult
    not_installed: bool = False
    incompatible: bool = False
    timed_out: bool = False
    blocked: bool = False  # Windows 정책이 Lens 실행 파일 자체를 막음(스마트 앱 제어 등)

    @property
    def readiness(self) -> str:
        if self.not_installed:
            return "미설치"
        # 시간이 모자란 것과 버전이 안 맞는 것은 사용자가 할 일이 정반대다(기다리기 vs
        # 업데이트). 예전엔 둘 다 "호환되지 않는 Lens 버전"으로 뭉뚱그려서, 느린 PC나
        # 텔레그램 DB가 큰 사용자에게 멀쩡한 Lens를 고장났다고 말했다 — 이 PC에서도
        # TelegramLens 진단이 6.2초 걸려 12초 제한을 넘길락 말락 했다.
        if self.timed_out:
            return "확인 시간 초과"
        # 미설치와 헷갈리면 안 된다 — 파일은 멀쩡히 있는데 Windows가 실행을 거절한
        # 상태라, "설치"나 "재설치"를 아무리 눌러도 그대로다.
        if self.blocked:
            return "Windows가 실행을 차단함"
        if self.incompatible:
            return "호환되지 않는 Lens 버전"
        if self.report is None:
            return "확인 실패"
        return self.report.readiness


def windows_block_message(process: ProcessResult) -> str:
    """Windows가 실행 파일 자체를 막았을 때(ProcessResult.error == "blocked") 화면 문구.

    진단·활성화·설치가 전부 같은 벽에 부딪히므로 문구를 한 곳에서만 만든다. 원인은
    거의 항상 Windows 11의 스마트 앱 제어다 — 서명 없는 exe를 차단하는데, uv가 만드는
    Lens 실행 파일이 정확히 거기 해당한다. 우리가 코드 서명을 붙이기 전까지는 사용자가
    그 스위치를 내리는 것 말고 방법이 없다.

    "한 번 끄면 다시 못 켠다"를 굳이 적는 이유: 사실이고(다시 켜려면 Windows 재설치),
    나중에 알게 되면 우리가 숨긴 게 된다.

    설정 앱 경로 대신 'Windows 보안' 앱 이름으로 안내한다 — 설정 트리의 이름은 빌드마다
    조금씩 다른데 앱 이름은 시작 메뉴 검색으로 바로 찾을 수 있다.
    """
    name = Path(process.cmd[0]).name if process.cmd else "프로그램"
    return (
        f"Windows가 '{name}' 실행을 차단했습니다(오류 4551). "
        "Windows 11의 '스마트 앱 제어'가 서명되지 않은 프로그램을 막을 때 생기는 현상이며, "
        "설치가 잘못된 것이 아닙니다. "
        "시작 메뉴에서 'Windows 보안'을 열고 → '앱 및 브라우저 컨트롤' → "
        "'스마트 앱 제어 설정' → '끄기'를 선택한 뒤 PC를 다시 시작하고 다시 시도해주세요"
        "(한 번 끄면 다시 켤 수 없습니다). "
        "회사·학교에서 관리하는 PC라면 관리자가 건 정책일 수 있어 관리자에게 문의해야 합니다."
    )


def diagnose_lens(
    lens: LensSpec, *, online: bool = False, timeout: float = DEFAULT_TIMEOUT
) -> LensDiagnosis:
    """`<lens>-doctor --json [--online]` 진단 + 부작용 없는 1회성 복구(lens_contract의
    auto_repair_after_install) 자동 적용.

    설치 직후에만 적용하면, 이미 예전에 설치돼 있던 Lens(재테스트·재설치 없이 자격증명만
    새로 설정하는 경우)는 대상에서 빠지는 문제가 실사용 중 발견됐다 — 그래서 모든 진단
    호출(설치 직후·대시보드 새로고침·마법사 재진단)이 똑같이 이 자동복구 혜택을 본다.
    한 번 고쳐지면 그 다음부터는 pending이 비어 이 분기 자체를 안 타므로, 매 호출마다
    추가 subprocess 비용이 드는 건 아니다. 복구 재확인은 딱 한 번만 시도한다(재귀 아님) —
    복구가 계속 실패하는 경우에도 무한 재시도로 빠지지 않는다. auto_repair_after_install
    목록엔 부작용 없는 항목만 올린다는 계약이 있어서(TelegramLens의 "daemon"처럼 외부
    상태 의존적인 건 애초에 이 목록에 없음) 매 진단마다 걸어도 안전하다."""
    diagnosis = _diagnose_lens_once(lens, online=online, timeout=timeout)
    if not diagnosis.report or not lens.auto_repair_after_install:
        return diagnosis
    pending = {c.repair_id for c in diagnosis.report.checks if c.repairable and c.repair_id}
    to_run = pending & set(lens.auto_repair_after_install)
    if not to_run:
        return diagnosis
    for repair_id in to_run:
        repair_lens(lens, repair_id)
    return _diagnose_lens_once(lens, online=online, timeout=timeout)  # 재확인 1회뿐 — 재귀 없음


def _diagnose_lens_once(
    lens: LensSpec, *, online: bool = False, timeout: float = DEFAULT_TIMEOUT
) -> LensDiagnosis:
    """`<lens>-doctor --json [--online]` 1회 호출(자동복구 없이 순수 진단만). 이 호출
    자체가 timeout을 가지므로 hang된 Lens 하나가 나머지 진단을 막지 않는다(호출자가
    순차로 여러 번 부르면 됨)."""
    cmd = [package_service.resolve_lens_command(lens.doctor_cmd), "--json"] + (["--online"] if online else [])
    process, payload = run_json_cli(cmd, timeout=timeout)

    # 이 Lens 가 --online 을 모르면 argparse 가 exit=2 로 죽고 stdout 에 JSON 이 없다 —
    # 그러면 아래에서 "호환되지 않는 Lens 버전"으로 떨어진다. 실사용에서 확인됐다:
    # TelegramLens 는 온라인 검사가 아예 없어서 이 옵션 자체가 없고, 지원 번들을
    # online=True 로 만들자 멀쩡한 TelegramLens 가 고장난 것처럼 보고됐다. 옵션을 빼고
    # 한 번 더 물어본다 — 온라인 검사만 못 할 뿐 나머지 진단은 그대로 쓸 수 있다.
    # 필드에 남아있는 옛 StockLens/DartLens 도 같은 이유로 여기서 구제된다.
    if online and payload is None and _online_flag_unsupported(process):
        process, payload = run_json_cli(cmd[:-1], timeout=timeout)

    if process.error == "not_found":
        return LensDiagnosis(lens=lens, report=None, process=process, not_installed=True)
    if process.error == "blocked":
        return LensDiagnosis(lens=lens, report=None, process=process, blocked=True)
    if process.timed_out:
        return LensDiagnosis(lens=lens, report=None, process=process, timed_out=True)
    # dict가 아닌 유효 JSON(배열·문자열·숫자)도 파서는 그대로 통과시킨다 — 그대로
    # DoctorReport.from_json에 넘기면 payload.get에서 AttributeError가 나고, 그걸
    # 잡는 곳이 없어 진단 전체(다른 Lens 포함)가 죽는다. "호환되지 않는 버전"으로
    # 떨어뜨려 한 Lens의 계약 위반이 대시보드 전체를 못 무너뜨리게 한다.
    if not isinstance(payload, dict):
        return LensDiagnosis(lens=lens, report=None, process=process, incompatible=True)

    try:
        report = DoctorReport.from_json(payload)
    except Exception:
        # 최상위는 dict인데 내부 타입이 계약과 다른 경우(license가 문자열, checks가
        # [null] 등)도 같은 이유로 여기서 흡수한다.
        return LensDiagnosis(lens=lens, report=None, process=process, incompatible=True)

    # schema_version이 안 맞아 "호환되지 않는 버전"으로 판정하더라도, installed_version은
    # 이미 파싱됐으니 최신 버전 조회는 그대로 해준다 — 실사용 중 발견된 문제: 호환 안
    # 되는 옛 버전일수록 "업데이트"가 정확한 해결책인데, 예전엔 이 판정 이전에
    # return해버려서 update_available이 끝까지 null로 남아 업데이트 버튼 자체가 안 떴다.
    if report.update_available is None and report.installed_version:
        _fill_update_info(lens, report)

    if report.schema_version not in (None, *SUPPORTED_SCHEMA_VERSIONS):
        return LensDiagnosis(lens=lens, report=report, process=process, incompatible=True)

    return LensDiagnosis(lens=lens, report=report, process=process)


def _online_flag_unsupported(process: ProcessResult) -> bool:
    """`--online` 을 몰라서 죽은 건지 판별. argparse 는 모르는 옵션에 exit=2 + stderr 에
    "unrecognized arguments: --online" 을 남긴다. 다른 이유의 exit=2(진짜 고장)를
    조용히 재시도로 덮지 않도록 두 신호를 모두 본다."""
    if process.exit_code != 2:
        return False
    combined = f"{process.stdout}\n{process.stderr}".lower()
    return "--online" in combined and "unrecognized arguments" in combined


def _fill_update_info(lens: LensSpec, report: DoctorReport) -> None:
    """doctor 자신은 --online일 때만(StockLens/DartLens) 또는 아예(TelegramLens) PyPI를
    확인하지 않는다 — 매번 무거운 --online(실 데이터소스 연결 확인 포함)을 강제하는 대신,
    PyPI 버전 조회 하나만 별도로 해서 update_available을 채운다. 실패해도(오프라인 등)
    조용히 None으로 남는다 — UI는 그 상태를 '확인 필요'로 표시한다."""
    latest = package_service.latest_pypi_version(lens.package_name, timeout=_UPDATE_CHECK_TIMEOUT)
    if not latest:
        return
    report.latest_version = latest
    report.update_available = package_service.version_gt(latest, report.installed_version)


def run_full_diagnosis(
    lenses: tuple[LensSpec, ...] = LENSES, *, online: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[LensDiagnosis]:
    """전체 진단 — Lens별 doctor JSON을 병렬이 아니라 순차 호출한다(2.4 진단 흐름).
    한 Lens가 timeout으로 끊겨도 다음 Lens 호출은 그대로 진행된다.

    `timeout`을 받는 이유: online=True는 Lens마다 실제 데이터소스에 붙어보기 때문에,
    네트워크가 죽은 PC에서는 Lens 하나가 기본 30초를 다 쓰고 끊긴다. 호출자가
    "3개 합쳐 이 시간 안에는 끝나야 한다"를 정할 수 있어야 한다(지원 번들 생성 등)."""
    return [diagnose_lens(lens, online=online, timeout=timeout) for lens in lenses]


def has_actionable_problem(diagnosis: LensDiagnosis) -> bool:
    """이 Lens가 사용자의 조치를 기다리고 있는지.

    각 Lens가 계산한 `overall`만 믿으면 안 된다 — 규칙이 서로 다르다. StockLens는
    `critical`이 아닌 실패(예: 엑셀 출력 폴더 쓰기 불가)를 `degraded`로 낮추는데,
    DartLens/TelegramLens는 실패 하나만 있어도 `fail`로 올린다. `overall == "fail"`만
    세면 StockLens의 실제 실패가 상단 요약의 "조치 필요"에서 통째로 빠진다.
    그래서 여기서는 checks를 직접 보고 판정한다(진행중 active·건너뜀은 문제가 아니다).
    """
    if diagnosis.not_installed or diagnosis.incompatible or diagnosis.timed_out:
        return True
    if diagnosis.report is None:
        return True  # 진단 자체를 못 한 상태 — 확인이 필요하다
    if diagnosis.report.overall == "fail":
        return True
    return any(c.status == "fail" for c in diagnosis.report.checks)


def summarize(diagnoses: list[LensDiagnosis]) -> dict:
    """대시보드 상단 요약 — "3개 중 2개 정상"/"업데이트 1개"/"조치 필요 1개" 형태(2.1)."""
    total = len(diagnoses)
    ok_count = sum(1 for d in diagnoses if d.report and d.report.overall == "ok" and not d.incompatible)
    update_count = sum(1 for d in diagnoses if d.report and d.report.update_available)
    action_count = sum(1 for d in diagnoses if has_actionable_problem(d))
    return {"total": total, "ok": ok_count, "update_available": update_count, "action_needed": action_count}


_CLAUDE_PAIR = {"claude-desktop", "claude-code"}


def _lens_command_names(lens: LensSpec) -> list[str]:
    """이 Lens가 만드는 실행 파일 이름들 — 파일을 쥐고 있는 프로세스를 찾을 때 쓴다.
    doctor/setup/activate 외에 트레이·데몬처럼 계약에 없는 것도 있으므로, 공통 접두어
    (예: "telegramlens")로 시작하는 실행 파일까지 포함되도록 접두어도 같이 넘긴다."""
    return [lens.doctor_cmd, lens.setup_cmd, lens.activate_cmd, lens.name,
            f"{lens.name}-tray", f"{lens.name}-daemon"]


def _unsupported_flag_message(lens: LensSpec, process: ProcessResult) -> str | None:
    """`payload is None`인 원인이 "이 Lens 버전이 이 옵션(--target codex 등)을 아직
    모른다"인지 판별한다 — Manager와 Lens 배포 버전이 어긋난 과도기(예: Codex 타겟을
    Manager는 이미 알지만 사용자가 실제로 설치한 Lens는 그 기능이 없는 옛 버전인 경우)에
    실사용 중 발견됨. argparse가 지원 안 하는 옵션/값을 만나면 exit=2로 종료하며
    stdout이 아니라 stderr에 "usage: ..."/"invalid choice"/"unrecognized arguments"를
    남긴다 — 이 신호가 보이면 "설치 실패"가 아니라 "업데이트 필요"로 안내해
    사용자가 원인 없는 파싱 에러 문구 대신 조치를 알 수 있게 한다."""
    if process.exit_code != 2:
        return None
    combined = f"{process.stdout}\n{process.stderr}".lower()
    if "invalid choice" not in combined and "unrecognized arguments" not in combined:
        return None
    return (
        f"설치된 {lens.display_name} 버전이 오래되어 이 등록 방식을 아직 지원하지 않습니다. "
        f"{lens.display_name}를 최신 버전으로 업데이트한 뒤 다시 시도해주세요."
    )


def setup_lens(
    lens: LensSpec, targets: list[str], *, timeout: float = DEFAULT_TIMEOUT
) -> SetupResult:
    """`<lens>-setup --target <t> --json --non-interactive`.

    Lens CLI의 `--target`은 한 번에 하나의 값(또는 claude-desktop+claude-code 조합인
    "both" 별칭)만 받는다 — Codex처럼 세 번째 타겟이 추가되면서 "claude-desktop과
    codex를 같이" 같은, "both"로 표현 못 하는 조합이 생겼다. claude-desktop+claude-code
    조합만 기존처럼 "both" 한 번 호출로 묶고, 그 외 타겟(codex 등)은 하나씩 별도
    호출한 뒤 결과를 병합한다 — 기존 "both"/단일 타겟 동작은 호출 횟수까지 그대로다."""
    if not targets:
        raise ValueError("targets가 비어 있습니다.")

    target_groups: list[str] = []
    remaining = list(dict.fromkeys(targets))  # 순서 보존 dedup
    if _CLAUDE_PAIR <= set(remaining):
        target_groups.append("both")
        remaining = [t for t in remaining if t not in _CLAUDE_PAIR]
    target_groups += remaining

    merged_targets: list = []
    ok = True
    error: str | None = None
    error_code: str | None = None
    raw: dict = {}
    for target_arg in target_groups:
        cmd = [package_service.resolve_lens_command(lens.setup_cmd), "--target", target_arg, "--json", "--non-interactive"]
        process, payload = run_json_cli(cmd, timeout=timeout)
        if process.error == "blocked":
            return SetupResult(ok=False, error_code="blocked", error=windows_block_message(process))
        if payload is None:
            ok = False
            error = _unsupported_flag_message(lens, process) or f"setup 응답을 파싱할 수 없습니다 (exit={process.exit_code})."
            continue
        result = SetupResult.from_json(payload, exit_code=process.exit_code)
        merged_targets.extend(result.targets)
        raw = result.raw
        if not result.ok:
            ok = False
            error = result.error
            error_code = result.error_code
    return SetupResult(ok=ok, targets=merged_targets, error=error, error_code=error_code, raw=raw)


def unregister_lens(
    lens: LensSpec, targets: list[str], *, timeout: float = DEFAULT_TIMEOUT
) -> SetupResult:
    """`<lens>-setup --target <t> --remove --json --non-interactive`.

    Manager의 "MCP 등록" 모달에서 체크를 풀면 여기로 온다. 예전엔 해제 수단이 아예
    없어서 체크박스가 토글처럼 보이는데 실제로는 "추가만" 됐다.

    setup_lens와 같은 이유로 타겟을 하나씩(또는 claude 쌍은 "both"로) 나눠 부른다 —
    Lens CLI의 --target이 한 번에 한 값만 받기 때문이다.
    """
    if not targets:
        raise ValueError("targets가 비어 있습니다.")

    target_groups: list[str] = []
    remaining = list(dict.fromkeys(targets))
    if _CLAUDE_PAIR <= set(remaining):
        target_groups.append("both")
        remaining = [t for t in remaining if t not in _CLAUDE_PAIR]
    target_groups += remaining

    ok = True
    error: str | None = None
    removed: list = []
    for target_arg in target_groups:
        cmd = [
            package_service.resolve_lens_command(lens.setup_cmd),
            "--target", target_arg, "--remove", "--json", "--non-interactive",
        ]
        process, payload = run_json_cli(cmd, timeout=timeout)
        if process.error == "blocked":
            return SetupResult(ok=False, error_code="blocked", error=windows_block_message(process))
        if payload is None:
            ok = False
            # 옛 버전 Lens에는 --remove가 없다 — 그 사실을 그대로 말해준다.
            error = _unsupported_flag_message(lens, process) or (
                f"해제 응답을 파싱할 수 없습니다 (exit={process.exit_code}). "
                "Lens를 최신 버전으로 업데이트한 뒤 다시 시도해주세요."
            )
            continue
        if not payload.get("ok"):
            ok = False
            error = payload.get("error") or "해제에 실패했습니다."
            continue
        removed.extend(payload.get("removed") or [])
    return SetupResult(ok=ok, targets=[], error=error, error_code=None, raw={"removed": removed})


def activate_lens(
    lens: LensSpec, license_key: str, *, timeout: float = DEFAULT_TIMEOUT
) -> ActivateResult:
    """`<lens>-activate --stdin --json`. 키 원문은 stdin으로만 전달 — 이 함수의 인자와
    반환값 어디에도 원문이 다시 나타나지 않는다(호출자도 license_key를 로깅하면 안 됨)."""
    cmd = [package_service.resolve_lens_command(lens.activate_cmd), "--stdin", "--json"]
    process, payload = run_json_cli(cmd, timeout=timeout, input_text=license_key)
    if process.error == "not_found":
        return ActivateResult(
            ok=False,
            error_code="not_installed",
            message=f"{lens.display_name}가 아직 설치되지 않았습니다. 먼저 '설치' 버튼을 눌러주세요.",
        )
    if process.error == "blocked":
        return ActivateResult(ok=False, error_code="blocked", message=windows_block_message(process))
    if payload is None:
        return ActivateResult(ok=False, message=f"activate 응답을 파싱할 수 없습니다 (exit={process.exit_code}).")
    return ActivateResult.from_json(payload, exit_code=process.exit_code)


def register_api_key(
    lens: LensSpec, credential_kind: str, api_key: str, *,
    targets: list[str] | None = None, timeout: float = DEFAULT_TIMEOUT,
) -> SetupResult:
    """`<lens>-setup --api-key-stdin --json --non-interactive [--target ...]`. 키 원문은
    stdin으로만 전달 — activate_lens와 동일한 보안 패턴(인자·반환값 어디에도 원문이 다시
    나타나지 않음). 라이선스 키 말고 추가 자격증명이 필요한 Lens(DartLens의 DART API 키)
    에서만 쓴다 — lens_contract에 등록되지 않은 credential_kind는 애초에 호출하지 않는다
    (repair_id 검증과 동일한 정신: 지원 여부를 문자열 파싱이 아니라 계약으로 판단)."""
    if credential_kind not in lens.extra_credentials:
        raise ValueError(
            f"{lens.display_name}는 credential_kind={credential_kind!r}를 지원하지 않습니다. "
            f"지원: {lens.extra_credentials or '(없음)'}"
        )
    base = [package_service.resolve_lens_command(lens.setup_cmd), "--api-key-stdin", "--json", "--non-interactive"]

    # --target을 안 주면 Lens는 "auto"로 떨어지는데, auto는 claude CLI와 Claude Desktop
    # 설정 폴더만 보고 **Codex는 절대 감지하지 않는다**(dartlens/setup_claude.py의
    # _resolve_targets). 그래서 Codex에 등록해둔 사람이 키를 넣으면 Codex 항목만 빠진
    # 채로 끝났다 — 키 자체는 OS 자격 증명 저장소에 들어가서 동작은 하지만, 결과에
    # Codex가 안 잡혀 "등록 안 됨"으로 보인다. 지금 등록돼 있는 곳을 그대로 넘긴다.
    #
    # 예전엔 targets를 받아도 `"both"` 아니면 `targets[0]` 하나로 접었다 — claude와
    # codex가 같이 등록된 사람은 codex가 통째로 버려졌다. setup_lens와 같은 방식으로
    # 나눠 부른다(Lens CLI의 --target이 한 번에 한 값만 받는다).
    target_groups: list[str] = []
    if targets:
        remaining = list(dict.fromkeys(targets))
        if _CLAUDE_PAIR <= set(remaining):
            target_groups.append("both")
            remaining = [t for t in remaining if t not in _CLAUDE_PAIR]
        target_groups += remaining
    else:
        target_groups = [""]  # --target 없이 한 번 (기존 auto 동작)

    merged_targets: list = []
    ok = True
    error: str | None = None
    error_code: str | None = None
    raw: dict = {}
    for target_arg in target_groups:
        cmd = base + (["--target", target_arg] if target_arg else [])
        process, payload = run_json_cli(cmd, timeout=timeout, input_text=api_key)
        if process.error == "not_found":
            return SetupResult(
                ok=False, error_code="not_installed",
                error=f"{lens.display_name}가 아직 설치되지 않았습니다. 먼저 '설치' 버튼을 눌러주세요.",
            )
        if process.error == "blocked":
            return SetupResult(ok=False, error_code="blocked", error=windows_block_message(process))
        if payload is None:
            ok = False
            error = _unsupported_flag_message(lens, process) or f"setup 응답을 파싱할 수 없습니다 (exit={process.exit_code})."
            continue
        result = SetupResult.from_json(payload, exit_code=process.exit_code)
        merged_targets.extend(result.targets)
        raw = result.raw
        if not result.ok:
            ok = False
            error = result.error
            error_code = result.error_code
    return SetupResult(ok=ok, targets=merged_targets, error=error, error_code=error_code, raw=raw)


def repair_lens(
    lens: LensSpec, repair_id: str, *, timeout: float = DEFAULT_TIMEOUT
) -> dict | None:
    """`<lens>-doctor --json --repair <repair_id> --yes`. lens_contract에 등록되지 않은
    repair_id는 애초에 호출하지 않는다(지원 여부를 문자열 파싱이 아니라 계약으로 판단)."""
    if repair_id not in lens.repair_ids:
        raise ValueError(
            f"{lens.display_name}는 repair_id={repair_id!r}를 지원하지 않습니다. "
            f"지원: {lens.repair_ids or '(없음)'}"
        )
    cmd = [package_service.resolve_lens_command(lens.doctor_cmd), "--json", "--repair", repair_id, "--yes"]
    _process, payload = run_json_cli(cmd, timeout=timeout)
    return payload


@dataclass
class UpdateResult:
    lens: LensSpec
    ok: bool
    previous_version: str | None
    target_version: str
    install: ProcessResult
    post_doctor: LensDiagnosis | None
    config_backup_paths: list[Path] = field(default_factory=list)
    rollback_command: str | None = None
    # 옛 pip 설치본을 정리했으면 그 실행 파일 경로. 화면이 "예전 설치본도 정리했다"고
    # 말해줄 수 있게 남긴다(사용자는 그런 게 있었는지도 모른다).
    legacy_pip_removed: str | None = None
    # 정리를 시도했는데 실패한 경우의 이유. 업데이트 자체는 성공으로 두되, 옛 것이
    # 남아 있다는 사실은 숨기지 않는다.
    legacy_pip_error: str | None = None


def update_lens(
    lens: LensSpec,
    target_version: str,
    *,
    previous_version: str | None = None,
    config_paths: list[Path] = (),
) -> UpdateResult:
    """업데이트 흐름(2.3) 1개 Lens 분: (config 백업) → `uv tool install --force` → doctor
    재확인. 실패해도 Manager가 알아서 되돌리지 않는다 — `rollback_command`만 채워서
    사용자가 스스로 실행할 수 있게 안내한다. 한 Lens 실패가 다른 Lens 상태를 가리지
    않도록, 이 함수는 예외를 던지지 않고 항상 UpdateResult를 반환한다."""
    backup_paths = [b for p in config_paths if (b := config_backup.backup_config(p)) is not None]

    install = package_service.install_version(lens.package_name, target_version)
    if not install.ok and package_service.looks_like_file_in_use(install):
        # 이 Lens가 띄워둔 보조 프로세스(트레이·데몬·MCP 서버)가 파일을 쥐고 있으면
        # uv가 폴더를 못 지워 설치가 통째로 실패한다 — 우리가 띄운 것들이므로 정리하고
        # 한 번만 다시 시도한다. Claude Desktop은 사용자 확인 없이 닫지 않는다(UI가 물어본다).
        if package_service.stop_processes_using_package(lens.package_name, _lens_command_names(lens)):
            install = package_service.install_version(lens.package_name, target_version)

    # 옛 `pip install` 잔재 정리 — **uv 설치가 성공한 뒤에만** 한다.
    #
    # 예전 배포가 pip 였기 때문에 그 시절 고객 PC 에는 시스템 Python 의 Scripts 에 실행
    # 파일이 남아 있다. PATH 순서상 그게 uv 관리본보다 먼저 잡혀서, Manager 는 최신을
    # 깔아놓고 "최신"이라 말하는데 터미널과 설정 파일은 옛 것을 가리킨다(이 PC 에서 실제로
    # 세 호스트 모두 옛 버전을 띄우고 있었다). 순서를 뒤집으면 — 먼저 지우고 설치가
    # 실패하면 — 고객에게 아무것도 안 남는다. 그래서 반드시 설치 성공 뒤다.
    legacy_removed: str | None = None
    legacy_error: str | None = None
    if install.ok:
        # **서버 커맨드까지** 본다. 실기기에서 확인: doctor/setup/activate 는 uv 쪽으로
        # 잡히는데 정작 MCP 설정 파일에 적히는 서버 커맨드(`stocklens`)만 옛 pip 쪽으로
        # 잡히는 상태가 실제로 있었다 — 셋만 보면 "잔재 없음"으로 지나친다.
        shadow = package_service.find_legacy_pip_shadow(_lens_command_names(lens))
        if shadow is not None:
            legacy = package_service.uninstall_legacy_pip_shadow(lens.package_name, shadow)
            if legacy.ok:
                legacy_removed = str(shadow)
            else:
                # 지우지 못해도 업데이트를 실패로 만들지 않는다 — 새 버전은 이미 깔렸고,
                # 등록 경로도 uv 관리본을 먼저 보게 돼 있다(각 Lens resolve_server_entry).
                legacy_error = redaction.redact(legacy.stderr) or "예전 설치본을 정리하지 못했습니다."

    post_doctor = diagnose_lens(lens) if install.ok else None  # diagnose_lens 자체가 안전한 자동복구를 이미 적용한다

    ok = bool(
        install.ok
        and post_doctor is not None
        and not post_doctor.incompatible
        and not post_doctor.not_installed
    )
    rollback_command = None
    if not ok and previous_version:
        rollback_command = f"uv tool install --force {lens.package_name}=={previous_version}"

    return UpdateResult(
        lens=lens,
        ok=ok,
        previous_version=previous_version,
        target_version=target_version,
        install=install,
        post_doctor=post_doctor,
        config_backup_paths=backup_paths,
        rollback_command=rollback_command,
        legacy_pip_removed=legacy_removed,
        legacy_pip_error=legacy_error,
    )


def check_for_updates(lenses: tuple[LensSpec, ...] = LENSES) -> dict[str, str | None]:
    """업데이트 흐름(2.3) 1단계 — 모든 Lens 최신 버전을 먼저 조회한다."""
    return {lens.name: package_service.latest_pypi_version(lens.package_name) for lens in lenses}


@dataclass
class UninstallResult:
    lens: LensSpec
    ok: bool
    uninstall: ProcessResult
    post_doctor: LensDiagnosis | None
    # 이 컴퓨터에 저장돼 있던 라이선스 키까지 지웠는지. 화면에 뭘 안내할지가 갈린다 —
    # 지웠으면 "다시 쓰려면 키를 다시 넣어야 한다"고 말해줘야 한다.
    license_removed: bool = False


def remove_license_file(lens: LensSpec) -> bool:
    """이 컴퓨터에 저장된 이 Lens의 라이선스 키를 지운다. 지웠으면 True.

    쓰던 컴퓨터를 정리할 때(팔거나, 넘기거나, 더 안 쓸 때) 라이선스가 남아 있으면
    다음 사람이 그대로 쓴다 — 삭제할 때 같이 지울지 물어보는 이유다.

    경로는 각 Lens licensing.py의 `_home()` 규칙과 같다(LensSpec.home_env/home_dir).
    홈을 옮겨 쓰는 사람도 있으므로 환경변수 우회를 그대로 따라간다."""
    if not lens.home_dir:
        return False
    base = os.environ.get(lens.home_env) if lens.home_env else None
    path = (Path(base) if base else Path.home() / lens.home_dir) / "license.key"
    try:
        if not path.is_file():
            return False
        path.unlink()
        return True
    except OSError:
        return False


def uninstall_lens(lens: LensSpec, *, remove_license: bool = False) -> UninstallResult:
    """`uv tool uninstall <package>` — 재설치가 필요한 경우(PATH에 uv 관리 밖의 낡은
    실행 파일이 남아 "호환되지 않는 버전"으로 계속 잡히는 등)를 위한 진입점.
    실패해도 예외를 던지지 않는다.

    기본값은 패키지만 지우고 라이선스 키·API 키는 남긴다 — "호환되지 않는 버전"을
    풀려고 지웠다 다시 까는 경우가 대부분이고, 그때마다 키를 다시 넣게 하면 안 된다.
    `remove_license=True`면 이 컴퓨터에 저장된 라이선스 키까지 지운다(쓰던 컴퓨터를
    정리하는 경우).

    uv로 설치된 것만 지우면 끝나지 않는 경우가 실사용 중 확인됐다 — 옛
    `pip install <lens>-mcp` 잔재가 PATH에서 uv가 새로 설치한 버전보다 먼저 잡히면,
    uv tool uninstall로 uv 쪽을 지워도 doctor는 여전히 그 낡은 pip 설치본을 찾아
    "호환되지 않는 버전"이 그대로 남는다. 그래서 uv 삭제 후 PATH에 uv 밖의 그림자
    실행 파일이 남아있는지 한 번 더 확인해서, 있으면 그것도 같이 지운다."""
    uv_result = package_service.uninstall_version(lens.package_name)
    if not uv_result.ok and package_service.looks_like_file_in_use(uv_result):
        # 설치와 같은 이유 — 이 Lens의 트레이·데몬·MCP 서버가 파일을 쥐고 있으면
        # 삭제도 "액세스가 거부되었습니다"로 막힌다(실사용에서 확인).
        if package_service.stop_processes_using_package(lens.package_name, _lens_command_names(lens)):
            uv_result = package_service.uninstall_version(lens.package_name)

    shadow = package_service.find_legacy_pip_shadow(_lens_command_names(lens))
    legacy_result = package_service.uninstall_legacy_pip_shadow(lens.package_name, shadow) if shadow else None

    overall_ok = uv_result.ok and (legacy_result is None or legacy_result.ok)
    representative = uv_result if not uv_result.ok else (legacy_result or uv_result)

    # 패키지를 지운 뒤에 키를 지운다 — 순서가 반대면 삭제가 실패했을 때 키만 날아간다.
    license_removed = remove_license_file(lens) if (remove_license and overall_ok) else False

    post_doctor = diagnose_lens(lens) if overall_ok else None
    ok = bool(overall_ok and post_doctor is not None and post_doctor.not_installed)
    return UninstallResult(
        lens=lens,
        ok=ok,
        uninstall=representative,
        post_doctor=post_doctor,
        license_removed=license_removed,
    )


# ── TelegramLens 로그인 마법사 ──────────────────────────────────────────
#
# 이 아래만 다른 Lens 명령들과 구조가 다르다 — 전화번호 → SMS 코드 → (필요하면) 2단계
# 인증까지 "여러 번 대화"가 필요해서, 한 번 실행하고 결과 받는 다른 CLI 호출과 달리
# 프로세스를 오래 살려둔 채로 상호작용한다(interactive_process.InteractiveProcess).
# 로그인은 한 번에 하나만 진행한다고 가정 — 세션은 모듈 전역 하나로 관리.

_login_session: "InteractiveProcess | None" = None
# pywebview는 JS 호출을 자기 스레드 풀에서 넘겨주므로 start/send/cancel이 서로 다른
# 스레드에서 겹칠 수 있다. 락 없이는 "취소 → (아직 None이라 no-op) → start가 대입"
# 순서가 가능해서, 모달은 닫혔는데 자식 프로세스는 살아남아 session.session을 계속
# 쥔다(다음 로그인이 SQLite 잠금으로 실패). 세션 하나를 다루는 구간 전체를 잠근다.
_login_lock = threading.Lock()


def start_telegram_login(*, timeout: float = 30.0) -> dict:
    """로그인 세션을 새로 시작하고 첫 상태(need_credentials 또는 need_phone 등)를
    반환한다. 이미 진행 중인 세션이 있으면 정리하고 새로 연다(모달을 닫지 않고 다시
    열었을 때 고아 프로세스가 쌓이지 않게)."""
    from leetkit_manager.interactive_process import InteractiveProcess

    global _login_session
    with _login_lock:
        _close_login_session_locked()

        cmd = [package_service.resolve_lens_command("telegramlens-login"), "--stepper"]
        try:
            session = InteractiveProcess(cmd)
        except OSError:
            # telegramlens-login 자체가 없으면(TelegramLens 미설치·PATH 문제) Popen이
            # FileNotFoundError를 던진다 — 예전엔 그대로 JS로 새어나가 모달이 "연결하는 중…"
            # 에서 영원히 멈췄다. 다른 실패와 같은 error 상태로 정규화해서 돌려준다.
            return {
                "status": "error", "code": "NOT_INSTALLED",
                "message": "TelegramLens가 설치되어 있지 않습니다. 먼저 '설치'를 진행해주세요.",
            }
        _login_session = session

    # read_first는 최대 timeout(30초)까지 블록한다 — 그동안 락을 쥐고 있으면 사용자가
    # 누른 취소가 30초 동안 먹통이 된다. 그래서 이 대기만 락 밖에서 한다.
    status = session.read_first(timeout=timeout)

    with _login_lock:
        if _login_session is not session:
            # 기다리는 사이 취소되었거나 다른 세션으로 교체됨 — 이 세션은 이미
            # 정리됐으므로 결과를 버린다(살아있는 세션을 건드리면 안 된다).
            return {"status": "error", "code": "CANCELLED", "message": "로그인이 취소되었습니다."}
        if status is None:
            _close_login_session_locked()
            return {"status": "error", "code": "START_FAILED", "message": "로그인 프로세스를 시작하지 못했습니다."}
    return status


def send_telegram_login_step(payload: dict, *, timeout: float = 30.0) -> dict:
    """현재 세션에 한 단계(전화번호/코드/비밀번호 등)를 보내고 다음 상태를 받는다.
    세션이 없으면(마법사를 안 거치고 호출된 경우) 에러 상태를 그대로 돌려준다."""
    with _login_lock:
        session = _login_session
    if session is None:
        return {"status": "error", "code": "NO_SESSION", "message": "로그인이 시작되지 않았습니다."}

    # send도 최대 timeout까지 블록하므로 락 밖에서 — 대신 끝난 뒤 이 세션이 아직
    # 현재 세션인지 확인해서, 기다리는 동안 취소된 경우의 결과를 흘리지 않는다.
    result = session.send(payload, timeout=timeout)
    with _login_lock:
        if _login_session is not session:
            return {"status": "error", "code": "CANCELLED", "message": "로그인이 취소되었습니다."}
    if result is None:
        return {"status": "error", "code": "NO_RESPONSE", "message": "응답을 받지 못했습니다(연결이 끊겼을 수 있습니다)."}
    return result


def _close_login_session_locked() -> None:
    """_login_lock을 이미 쥔 상태에서만 호출한다."""
    global _login_session
    if _login_session is not None:
        _login_session.close()
        _login_session = None


def cancel_telegram_login() -> None:
    """모달을 취소하거나 창을 닫을 때 호출 — 세션이 남아있으면 자식 프로세스를 정리한다."""
    with _login_lock:
        _close_login_session_locked()

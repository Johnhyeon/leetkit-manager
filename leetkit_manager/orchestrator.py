"""설치·진단·업데이트 상태 머신 — UI가 직접 subprocess/uv/config 파일을 만지지 않도록
모든 Lens 조작을 이 모듈 하나로 모은다. `ui/`는 여기 함수들의 반환값을 표시만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from leetkit_manager import config_backup, package_service
from leetkit_manager.lens_contract import LENSES, LensSpec
from leetkit_manager.models import ActivateResult, DoctorReport, SetupResult
from leetkit_manager.process_runner import DEFAULT_TIMEOUT, ProcessResult, run_json_cli

# Manager가 이해하는 doctor JSON schema_version. 이 범위 밖(또는 파싱 자체가 안 되는)
# Lens는 "호환되지 않는 Lens 버전"으로 표시한다(공통 수용 기준).
SUPPORTED_SCHEMA_VERSIONS = (1,)


@dataclass
class LensDiagnosis:
    lens: LensSpec
    report: DoctorReport | None
    process: ProcessResult
    not_installed: bool = False
    incompatible: bool = False

    @property
    def readiness(self) -> str:
        if self.not_installed:
            return "미설치"
        if self.incompatible:
            return "호환되지 않는 Lens 버전"
        if self.report is None:
            return "확인 실패"
        return self.report.readiness


def diagnose_lens(
    lens: LensSpec, *, online: bool = False, timeout: float = DEFAULT_TIMEOUT
) -> LensDiagnosis:
    """`<lens>-doctor --json [--online]` 1회 호출. 이 호출 자체가 timeout을 가지므로
    hang된 Lens 하나가 나머지 진단을 막지 않는다(호출자가 순차로 여러 번 부르면 됨)."""
    cmd = [package_service.resolve_lens_command(lens.doctor_cmd), "--json"] + (["--online"] if online else [])
    process, payload = run_json_cli(cmd, timeout=timeout)

    if process.error == "not_found":
        return LensDiagnosis(lens=lens, report=None, process=process, not_installed=True)
    if payload is None:
        return LensDiagnosis(lens=lens, report=None, process=process, incompatible=True)

    report = DoctorReport.from_json(payload)
    if report.schema_version not in (None, *SUPPORTED_SCHEMA_VERSIONS):
        return LensDiagnosis(lens=lens, report=report, process=process, incompatible=True)
    return LensDiagnosis(lens=lens, report=report, process=process)


def run_full_diagnosis(
    lenses: tuple[LensSpec, ...] = LENSES, *, online: bool = False
) -> list[LensDiagnosis]:
    """전체 진단 — Lens별 doctor JSON을 병렬이 아니라 순차 호출한다(2.4 진단 흐름).
    한 Lens가 30초 timeout으로 끊겨도 다음 Lens 호출은 그대로 진행된다."""
    return [diagnose_lens(lens, online=online) for lens in lenses]


def summarize(diagnoses: list[LensDiagnosis]) -> dict:
    """대시보드 상단 요약 — "3개 중 2개 정상"/"업데이트 1개"/"조치 필요 1개" 형태(2.1)."""
    total = len(diagnoses)
    ok_count = sum(1 for d in diagnoses if d.report and d.report.overall == "ok" and not d.incompatible)
    update_count = sum(1 for d in diagnoses if d.report and d.report.update_available)
    action_count = sum(
        1 for d in diagnoses
        if d.not_installed or d.incompatible or (d.report and d.report.overall == "fail")
    )
    return {"total": total, "ok": ok_count, "update_available": update_count, "action_needed": action_count}


def setup_lens(
    lens: LensSpec, targets: list[str], *, timeout: float = DEFAULT_TIMEOUT
) -> SetupResult:
    """`<lens>-setup --target <t> --json --non-interactive`."""
    if set(targets) >= {"claude-desktop", "claude-code"}:
        target_arg = "both"
    elif targets:
        target_arg = targets[0]
    else:
        raise ValueError("targets가 비어 있습니다.")

    cmd = [package_service.resolve_lens_command(lens.setup_cmd), "--target", target_arg, "--json", "--non-interactive"]
    process, payload = run_json_cli(cmd, timeout=timeout)
    if payload is None:
        return SetupResult(ok=False, error=f"setup 응답을 파싱할 수 없습니다 (exit={process.exit_code}).")
    return SetupResult.from_json(payload, exit_code=process.exit_code)


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
    if payload is None:
        return ActivateResult(ok=False, message=f"activate 응답을 파싱할 수 없습니다 (exit={process.exit_code}).")
    return ActivateResult.from_json(payload, exit_code=process.exit_code)


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
    post_doctor = diagnose_lens(lens) if install.ok else None

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
    )


def check_for_updates(lenses: tuple[LensSpec, ...] = LENSES) -> dict[str, str | None]:
    """업데이트 흐름(2.3) 1단계 — 모든 Lens 최신 버전을 먼저 조회한다."""
    return {lens.name: package_service.latest_pypi_version(lens.package_name) for lens in lenses}

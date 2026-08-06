"""세 Lens의 doctor/setup/activate `--json` 출력을 파싱하는 공통 데이터 모델.

Manager 공통 계약(LeetKit Manager Program Requirements 3절)이 정의하는 최상위 필드
이름은 세 Lens 모두 문자 그대로 동일하다(doctor: schema_version/product/package_name/
installed_version/latest_version/update_available/overall/checked_at/online/license/
targets/checks. checks[] 항목: id/status/summary/details/repairable/repair_id/action).

단, activate/setup의 "성공했는가" 최상위 필드는 Lens마다 이름이 다르다 — DartLens는
`api_key_saved`/`license_activated`를 자기 자신의 다른 두 작업(DART API 키 등록 vs 상품
라이선스 활성화)과 혼동하지 않으려고 의도적으로 `ok`를 안 쓴다(DartLens readiness 문서
Task 4 요구사항). 이 차이는 여기 파서에서 흡수하고, Lens 쪽 필드 이름은 더 건드리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CheckResult:
    id: str
    status: str  # "ok" | "active" | "warn" | "fail" | "skip" | "info-skip"
    # "active" = 정상이지만 지금 뭔가 진행 중(예: TelegramLens 백필) — 문제로 세지 않되
    # "정상"과도 구분해 UI에서 별도 "진행중"으로 보여준다.
    summary: str
    details: dict = field(default_factory=dict)
    repairable: bool = False
    repair_id: str | None = None
    action: str | None = None

    @classmethod
    def from_json(cls, payload: dict) -> "CheckResult":
        return cls(
            id=payload.get("id", ""),
            status=payload.get("status") or "unknown",
            summary=payload.get("summary", ""),
            details=payload.get("details") or {},
            repairable=bool(payload.get("repairable", False)),
            repair_id=payload.get("repair_id"),
            action=payload.get("action"),
        )


@dataclass
class LicenseInfo:
    status: str  # "active" | "missing" | "invalid"
    license_id_masked: str | None = None

    @classmethod
    def from_json(cls, payload: dict | None) -> "LicenseInfo":
        payload = payload or {}
        return cls(
            status=payload.get("status") or "unknown",
            license_id_masked=payload.get("license_id_masked"),
        )


@dataclass
class DoctorReport:
    schema_version: int | None
    product: str
    package_name: str
    installed_version: str | None
    latest_version: str | None
    update_available: bool | None
    overall: str  # "ok" | "degraded" | "fail"
    checked_at: str | None
    online: bool
    license: LicenseInfo
    targets: list[str]
    checks: list[CheckResult]
    # 원본 payload 전체 — DartLens의 dart_api/corp_code_cache 같은 Lens 고유 확장
    # 필드는 공통 모델에 없으니 필요하면 여기서 직접 꺼내 쓴다.
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict) -> "DoctorReport":
        return cls(
            schema_version=payload.get("schema_version"),
            product=payload.get("product", ""),
            package_name=payload.get("package_name", ""),
            installed_version=payload.get("installed_version"),
            latest_version=payload.get("latest_version"),
            update_available=payload.get("update_available"),
            overall=payload.get("overall") or "unknown",
            checked_at=payload.get("checked_at"),
            online=bool(payload.get("online", False)),
            license=LicenseInfo.from_json(payload.get("license")),
            targets=list(payload.get("targets") or []),
            checks=[CheckResult.from_json(c) for c in (payload.get("checks") or [])],
            raw=payload,
        )

    @property
    def readiness(self) -> str:
        """대시보드 "핵심 준비 상태" 3단계(정상/주의/조치 필요) — overall을 한글 라벨로."""
        return {"ok": "정상", "degraded": "주의", "fail": "조치 필요"}.get(self.overall, "확인 필요")


@dataclass
class ActivateResult:
    ok: bool
    license_id_masked: str | None = None
    error_code: str | None = None
    message: str | None = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict, *, exit_code: int | None = None) -> "ActivateResult":
        ok = payload.get("ok")
        if ok is None:
            ok = payload.get("license_activated")
        if ok is None:
            ok = exit_code == 0 if exit_code is not None else False
        return cls(
            ok=bool(ok),
            license_id_masked=payload.get("license_id_masked"),
            error_code=payload.get("error_code"),
            message=payload.get("message") or payload.get("reason"),
            raw=payload,
        )


@dataclass
class SetupResult:
    ok: bool
    # StockLens/TelegramLens: [{"target_label", "config_path", "backup_path", "command", ...}, ...]
    # DartLens(API 키 setup): ["claude-code", ...] 처럼 슬러그 문자열 리스트일 수 있다 —
    # 강제로 통일하지 않고 그대로 둔다. 필요하면 raw로 원본 접근.
    targets: list = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, payload: dict, *, exit_code: int | None = None) -> "SetupResult":
        ok = payload.get("ok")
        if ok is None:
            ok = payload.get("api_key_saved")
        if ok is None:
            ok = exit_code == 0 if exit_code is not None else False
        targets = payload.get("targets")
        return cls(
            ok=bool(ok),
            targets=list(targets) if isinstance(targets, list) else [],
            error=payload.get("error"),
            error_code=payload.get("error_code"),
            raw=payload,
        )

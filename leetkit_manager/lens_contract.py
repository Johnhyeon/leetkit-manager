"""세 Lens(StockLens/DartLens/TelegramLens)의 고정 메타데이터.

Manager는 Lens를 동적으로 발견하지 않는다 — v1 대상은 이 세 개로 확정되어 있다
(LeetKit Manager Program Requirements 1.2). 여기 정의된 커맨드 이름·지원 repair ID는
각 Lens 패키지의 실제 CLI 계약과 정확히 일치해야 한다 — 어긋나면 Manager가 존재하지
않는 명령을 부르게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LensSpec:
    name: str  # "stocklens" | "dartlens" | "telegramlens" — DoctorReport.product과 일치
    display_name: str  # 대시보드에 보여줄 이름
    package_name: str  # PyPI 배포명 — uv tool install 대상
    doctor_cmd: str
    setup_cmd: str
    activate_cmd: str
    # 이 Lens의 `<cmd> --repair <id> --yes`가 지원하는 repair id 전체.
    # 비어 있으면 이 Lens는 자동 복구를 전혀 지원하지 않는다(명령 안내만 가능).
    repair_ids: tuple[str, ...] = field(default_factory=tuple)
    # 라이선스 키 외에 추가로 필요한 자격증명 종류. DartLens만 "dart_api"를 가진다 —
    # doctor JSON의 최상위 `dart_api` 확장 필드와 setup의 `--api-key-stdin`에 대응.
    extra_credentials: tuple[str, ...] = field(default_factory=tuple)


STOCKLENS = LensSpec(
    name="stocklens",
    display_name="StockLens",
    package_name="stocklens-mcp",
    doctor_cmd="stocklens-doctor",
    setup_cmd="stocklens-setup",
    activate_cmd="stocklens-activate",
    repair_ids=(),
)

DARTLENS = LensSpec(
    name="dartlens",
    display_name="DartLens",
    package_name="dartlens-mcp",
    doctor_cmd="dartlens-doctor",
    setup_cmd="dartlens-setup",
    activate_cmd="dartlens-activate",
    repair_ids=("corp-code-cache",),
    extra_credentials=("dart_api",),
)

TELEGRAMLENS = LensSpec(
    name="telegramlens",
    display_name="TelegramLens",
    package_name="telegramlens-mcp",
    doctor_cmd="telegramlens-doctor",
    setup_cmd="telegramlens-setup",
    activate_cmd="telegramlens-activate",
    # 실제 구현은 daemon/status-files를 하나의 scope로 합쳐 두었다 — repair_id "daemon"
    # 하나로 데몬 좀비 정리+상태파일 복구+정체 백필 표시를 모두 수행한다.
    repair_ids=("daemon",),
)

# 대시보드 표시 순서 — 세트 구성(기본/풀세트) 순서와 동일하게 유지.
LENSES: tuple[LensSpec, ...] = (STOCKLENS, DARTLENS, TELEGRAMLENS)

_BY_NAME: dict[str, LensSpec] = {lens.name: lens for lens in LENSES}


def get_lens(name: str) -> LensSpec:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise ValueError(f"Unknown lens: {name!r}. Valid: {list(_BY_NAME)}") from None

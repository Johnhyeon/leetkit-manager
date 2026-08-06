"""문서 6절 7단계 — 실제 uv tool 환경을 쓰는 통합 테스트.

다른 테스트들과 달리 이 파일은 실제로 전역 설치된 `stocklens-doctor`/`dartlens-doctor`/
`telegramlens-doctor`를 호출한다. 개발 머신처럼 세 Lens가 uv tool로 설치돼 있어야
의미가 있으므로 `integration` 마커를 붙여 기본 스위트에서는 빠지게 한다.

    pytest -m integration          # 이 파일만 실행
    pytest -m "not integration"    # 기본 스위트(다른 모든 테스트)에서 이 파일 제외

설치가 안 돼 있으면 not_installed=True로 잡히는 것도 정상 동작이므로 실패로 보지 않고
스킵한다 — 이 테스트의 목적은 "실제 설치본과 Manager가 실제로 대화 가능한가"이지,
"이 머신에 반드시 세 Lens가 설치돼 있어야 한다"가 아니다.
"""

from __future__ import annotations

import pytest

from leetkit_manager import orchestrator
from leetkit_manager.lens_contract import LENSES

pytestmark = pytest.mark.integration


@pytest.fixture(params=LENSES, ids=[lens.name for lens in LENSES])
def lens(request):
    return request.param


def test_real_doctor_json_is_parseable_and_schema_compatible(lens):
    diag = orchestrator.diagnose_lens(lens)
    if diag.not_installed:
        pytest.skip(f"{lens.display_name}가 이 머신에 설치돼 있지 않음 — 통합 테스트 대상 아님")

    assert diag.incompatible is False, (
        f"{lens.display_name} doctor --json이 Manager 공통 계약과 안 맞습니다. "
        f"PATH가 uv tool 최신 버전이 아니라 다른(구버전) 실행 파일을 가리키고 있을 수 있습니다."
    )
    assert diag.report is not None
    assert diag.report.schema_version == 1
    assert diag.report.product == lens.name
    assert diag.report.installed_version


def test_run_full_diagnosis_against_real_installs_does_not_raise():
    diagnoses = orchestrator.run_full_diagnosis()
    assert len(diagnoses) == len(LENSES)
    summary = orchestrator.summarize(diagnoses)
    assert summary["total"] == len(LENSES)

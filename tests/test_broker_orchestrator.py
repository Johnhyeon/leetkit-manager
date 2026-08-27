"""증권사 연결 오케스트레이션 단위 테스트 (Task 15).

실제 CLI 에 의존하지 않는다. run_json_cli 를 patch 해서 "이 JSON 이 오면
이렇게 해석한다"만 검증한다. 비밀값은 stdin JSON 으로만 흐른다.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from conftest import load_fixture

from leetkit_manager import orchestrator
from leetkit_manager.lens_contract import DARTLENS, STOCKLENS, TELEGRAMLENS
from leetkit_manager.models import BrokerActionResult, DoctorReport
from leetkit_manager.process_runner import ProcessResult

SENTINEL_KEY = "PSA-SENTINEL-APP-KEY-M15"
SENTINEL_SECRET = "PSA-SENTINEL-APP-SECRET-M15"


def _proc(exit_code=0, error=None) -> ProcessResult:
    return ProcessResult(
        cmd=["stocklens-broker", "--json", "--non-interactive", "--stdin"],
        exit_code=exit_code, stdout="", stderr="", timed_out=False,
        duration_s=0.1, error=error)


def _ok_payload(action: str, **extra) -> dict:
    payload = {
        "ok": True, "contract_version": 1, "action": action,
        "status": {
            "provider": "kis", "connection_generation": 2,
            "active_provider": "kis", "active_profile": "real",
            "data_source_mode": "auto",
            "profiles": {"real": {"configured": True},
                         "demo": {"configured": False}},
        },
    }
    payload.update(extra)
    return payload


class TestBrokerSpec:
    def test_stocklens_has_broker_spec(self):
        spec = STOCKLENS.broker
        assert spec is not None
        assert spec.command == "stocklens-broker"
        assert spec.contract_version == 1
        assert spec.providers == ("kis",)

    def test_other_lenses_have_no_broker_spec(self):
        assert DARTLENS.broker is None
        assert TELEGRAMLENS.broker is None


class TestBrokerSupportDetection:
    def test_new_doctor_advertises_support(self):
        payload = load_fixture("stocklens_doctor.json")
        report = DoctorReport.from_json(payload)
        assert orchestrator.broker_supported(STOCKLENS, report) is True

    def test_old_doctor_without_capabilities_is_update_required(self):
        payload = load_fixture("stocklens_doctor.json")
        payload = {k: v for k, v in payload.items()
                   if k not in ("capabilities", "provider_connections")}
        report = DoctorReport.from_json(payload)
        assert orchestrator.broker_supported(STOCKLENS, report) is False

    def test_report_exposes_provider_connections(self):
        payload = load_fixture("stocklens_doctor.json")
        report = DoctorReport.from_json(payload)
        assert "kis" in report.provider_connections


class TestBrokerAction:
    def _run(self, action, payload=None, process=None, **kwargs):
        with patch.object(
            orchestrator, "run_json_cli",
            return_value=(process or _proc(), payload),
        ) as mock_run, patch(
            "leetkit_manager.orchestrator.package_service."
            "resolve_lens_command", side_effect=lambda name: name,
        ):
            result = orchestrator.broker_action(
                STOCKLENS, action, **kwargs)
        return result, mock_run

    def test_status_roundtrip(self):
        result, mock_run = self._run("status", _ok_payload("status"))
        assert result.ok
        assert result.status["active_profile"] == "real"
        cmd = mock_run.call_args.args[0]
        assert cmd == ["stocklens-broker", "--json", "--non-interactive",
                       "--stdin"]

    def test_secrets_travel_through_stdin_only(self):
        result, mock_run = self._run(
            "verify_and_save", _ok_payload("verify_and_save"),
            profile="real",
            credentials={"app_key": SENTINEL_KEY,
                         "app_secret": SENTINEL_SECRET})
        assert result.ok
        cmd = mock_run.call_args.args[0]
        assert SENTINEL_KEY not in " ".join(cmd)
        sent = json.loads(mock_run.call_args.kwargs["input_text"])
        assert sent["contract_version"] == 1
        assert sent["action"] == "verify_and_save"
        assert sent["credentials"]["app_key"] == SENTINEL_KEY
        # 반환값 어디에도 비밀 원문이 없다.
        assert SENTINEL_KEY not in json.dumps(result.raw)
        assert SENTINEL_KEY not in (result.message or "")

    @pytest.mark.parametrize("action,extra", [
        ("verify", {"profile": "real",
                    "credentials": {"app_key": "k", "app_secret": "s"}}),
        ("switch_profile", {"profile": "demo"}),
        ("disconnect_profile", {"profile": "real"}),
        ("disconnect_provider", {}),
        ("set_data_source_mode", {"mode": "legacy"}),
    ])
    def test_all_actions_send_contract_request(self, action, extra):
        _, mock_run = self._run(action, _ok_payload(action), **extra)
        sent = json.loads(mock_run.call_args.kwargs["input_text"])
        assert sent["action"] == action
        assert sent["provider"] == "kis"
        assert sent["contract_version"] == 1

    def test_unknown_action_rejected_before_cli(self):
        with pytest.raises(ValueError):
            orchestrator.broker_action(STOCKLENS, "format_disk")

    def test_unknown_provider_rejected_before_cli(self):
        with pytest.raises(ValueError):
            orchestrator.broker_action(STOCKLENS, "status",
                                       provider="unknown_sec")

    def test_lens_without_broker_spec_is_unsupported(self):
        result = orchestrator.broker_action(DARTLENS, "status")
        assert not result.ok
        assert result.error_code == "broker_unsupported"

    def test_missing_command_maps_to_update_required(self):
        result, _ = self._run(
            "status", None, process=_proc(error="not_found"))
        assert not result.ok
        assert result.error_code == "update_required"
        assert "업데이트" in result.message

    def test_unsupported_contract_version_error_passthrough(self):
        payload = {
            "ok": False, "contract_version": 1,
            "error": {"code": "unsupported_contract_version",
                      "message": "지원하는 contract_version은 1입니다"},
        }
        result, _ = self._run("status", payload, process=_proc(exit_code=1))
        assert not result.ok
        assert result.error_code == "unsupported_contract_version"

    def test_unparseable_response_is_error(self):
        result, _ = self._run("status", None, process=_proc(exit_code=1))
        assert not result.ok
        assert result.error_code == "parse_error"

    def test_verification_results_exposed(self):
        payload = _ok_payload("verify_and_save", verification={
            "auth": "ok", "kr_intraday": "available",
            "us_intraday": "unavailable"})
        result, _ = self._run(
            "verify_and_save", payload, profile="real",
            credentials={"app_key": "k", "app_secret": "s"})
        assert result.verification["kr_intraday"] == "available"
        assert result.verification["us_intraday"] == "unavailable"


class TestBrokerActionResultModel:
    def test_from_json_error_shape(self):
        result = BrokerActionResult.from_json({
            "ok": False,
            "error": {"code": "credential_invalid", "message": "인증 실패"},
        }, exit_code=1)
        assert not result.ok
        assert result.error_code == "credential_invalid"
        assert result.message == "인증 실패"


class TestStatusUnavailablePassthrough:
    """리뷰 지적: 커밋 성공 + 상태 재조회 실패(status_unavailable)가
    전달 계층에서 버려져 UI 가 그냥 "연결 완료"를 띄웠다."""

    def test_model_carries_status_unavailable_and_warnings(self):
        result = BrokerActionResult.from_json({
            "ok": True, "contract_version": 1, "action": "verify_and_save",
            "status": {"active_profile": "real"},
            "status_unavailable": True,
            "warnings": ["변경은 저장됐지만 상태 재조회에 실패했습니다"],
        }, exit_code=0)
        assert result.ok
        assert result.status_unavailable is True
        assert any("재조회" in w for w in result.warnings)

    def test_model_defaults_when_absent(self):
        result = BrokerActionResult.from_json({
            "ok": True, "status": {}}, exit_code=0)
        assert result.status_unavailable is False
        assert result.warnings == []

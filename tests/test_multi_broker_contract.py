"""Manager 멀티 증권사 계약 테스트 (1.0 Task 20).

- StockLens describe_providers 로 동적 descriptor 를 받는다
- provider 별 credential 필드를 이름 그대로 stdin JSON 에 싣는다
  (Manager 가 필드명을 복제·개명하지 않는다)
- set_primary_provider / recover_cleanup 중계
- 구 StockLens 는 새 공급자를 숨긴다 (KIS 전용으로 동작)
- v1 KIS 요청 호환 유지
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from conftest import load_fixture

from leetkit_manager import orchestrator
from leetkit_manager.lens_contract import STOCKLENS
from leetkit_manager.models import DoctorReport
from leetkit_manager.process_runner import ProcessResult

SENTINEL_ID = "PSA-M20-CLIENT-ID"
SENTINEL_SECRET = "PSA-M20-CLIENT-SECRET"

_DESCRIBE_PAYLOAD = {
    "ok": True, "contract_version": 1, "action": "describe_providers",
    "providers": [
        {"provider_id": "kis", "display_name": "한국투자증권",
         "supported_profiles": ["real", "demo"],
         "signup_url": "https://apiportal.koreainvestment.com",
         "docs_url": "https://apiportal.koreainvestment.com/docs",
         "credential_fields": [
             {"name": "app_key", "label": "앱 키", "secret": True,
              "max_length": 2048},
             {"name": "app_secret", "label": "앱 시크릿", "secret": True,
              "max_length": 2048}]},
        {"provider_id": "kiwoom", "display_name": "키움증권",
         "supported_profiles": ["real", "demo"],
         "signup_url": "https://openapi.kiwoom.com",
         "docs_url": "https://openapi.kiwoom.com/m/guide/apiguide",
         "credential_fields": [
             {"name": "app_key", "label": "앱 키", "secret": True,
              "max_length": 2048},
             {"name": "secret_key", "label": "시크릿 키", "secret": True,
              "max_length": 2048}]},
        {"provider_id": "toss", "display_name": "토스증권",
         "supported_profiles": ["real"],
         "signup_url": "https://corp.tossinvest.com/ko/open-api",
         "docs_url": "https://developers.tossinvest.com/docs",
         "credential_fields": [
             {"name": "client_id", "label": "클라이언트 ID",
              "secret": True, "max_length": 2048},
             {"name": "client_secret", "label": "클라이언트 시크릿",
              "secret": True, "max_length": 2048}]},
    ],
}


def _proc(exit_code=0, error=None) -> ProcessResult:
    return ProcessResult(
        cmd=["stocklens-broker"], exit_code=exit_code, stdout="",
        stderr="", timed_out=False, duration_s=0.1, error=error)


def _run(action, payload=None, process=None, **kwargs):
    with patch.object(
        orchestrator, "run_json_cli",
        return_value=(process or _proc(), payload),
    ) as mock_run, patch(
        "leetkit_manager.orchestrator.package_service."
        "resolve_lens_command", side_effect=lambda name: name,
    ):
        result = orchestrator.broker_action(STOCKLENS, action, **kwargs)
    return result, mock_run


class TestSpec:
    def test_stocklens_supports_three_providers(self):
        assert STOCKLENS.broker.providers == ("kis", "kiwoom", "toss")


class TestDescribeProviders:
    def _describe(self, payload=None, process=None):
        with patch.object(
            orchestrator, "run_json_cli",
            return_value=(process or _proc(), payload),
        ), patch(
            "leetkit_manager.orchestrator.package_service."
            "resolve_lens_command", side_effect=lambda name: name,
        ):
            return orchestrator.broker_describe_providers(STOCKLENS)

    def test_dynamic_descriptors_from_stocklens(self):
        providers = self._describe(payload=_DESCRIBE_PAYLOAD)
        assert [p["provider_id"] for p in providers] == \
            ["kis", "kiwoom", "toss"]
        kiwoom = providers[1]
        assert [f["name"] for f in kiwoom["credential_fields"]] == \
            ["app_key", "secret_key"]

    def test_old_stocklens_hides_new_providers(self):
        # 구 CLI 는 unknown_action 을 돌려준다 -> None (KIS 전용 fallback).
        payload = {"ok": False, "contract_version": 1,
                   "error": {"code": "unknown_action",
                             "message": "지원하지 않는 action"}}
        assert self._describe(
            payload=payload, process=_proc(exit_code=1)) is None

    def test_missing_command_hides_new_providers(self):
        assert self._describe(
            payload=None, process=_proc(error="not_found")) is None


class TestGenericCredentials:
    def test_toss_credentials_pass_through_unrenamed(self):
        result, mock_run = _run(
            "verify_and_save",
            {"ok": True, "contract_version": 1,
             "action": "verify_and_save", "status": {}},
            provider="toss", profile="real",
            credentials={"client_id": SENTINEL_ID,
                         "client_secret": SENTINEL_SECRET})
        assert result.ok
        sent = json.loads(mock_run.call_args.kwargs["input_text"])
        assert sent["provider"] == "toss"
        assert sent["credentials"] == {
            "client_id": SENTINEL_ID, "client_secret": SENTINEL_SECRET}
        cmd = mock_run.call_args.args[0]
        assert SENTINEL_SECRET not in " ".join(cmd)

    def test_kiwoom_credentials_pass_through(self):
        _, mock_run = _run(
            "verify",
            {"ok": True, "contract_version": 1, "action": "verify",
             "status": {}},
            provider="kiwoom", profile="real",
            credentials={"app_key": "k", "secret_key": "s"})
        sent = json.loads(mock_run.call_args.kwargs["input_text"])
        assert sent["credentials"] == {"app_key": "k", "secret_key": "s"}

    def test_empty_credential_value_rejected_before_cli(self):
        result, mock_run = _run(
            "verify_and_save", None,
            provider="toss", profile="real",
            credentials={"client_id": "x", "client_secret": "  "})
        assert not result.ok
        assert result.error_code == "invalid_request"
        assert mock_run.call_count == 0

    def test_missing_credentials_rejected(self):
        result, mock_run = _run(
            "verify", None, provider="kiwoom", profile="real",
            credentials=None)
        assert not result.ok
        assert mock_run.call_count == 0


class TestPrimaryActions:
    def test_set_primary_provider_relay(self):
        result, mock_run = _run(
            "set_primary_provider",
            {"ok": True, "contract_version": 1,
             "action": "set_primary_provider",
             "status": {"primary_provider": "toss"}},
            provider="toss")
        assert result.ok
        sent = json.loads(mock_run.call_args.kwargs["input_text"])
        assert sent == {"contract_version": 1,
                        "action": "set_primary_provider",
                        "provider": "toss"}

    def test_recover_cleanup_relay(self):
        result, mock_run = _run(
            "recover_cleanup",
            {"ok": True, "contract_version": 1,
             "action": "recover_cleanup", "status": {},
             "recovered": {"removed": [], "failed": []}},
            provider="kiwoom")
        assert result.ok
        sent = json.loads(mock_run.call_args.kwargs["input_text"])
        assert sent["action"] == "recover_cleanup"


class TestDoctorCapabilities:
    def test_new_capabilities_accessors(self):
        report = DoctorReport.from_json(load_fixture(
            "stocklens_doctor.json"))
        assert report.broker_connection_contract == 1
        assert report.broker_provider_registry_contract == 1
        assert report.multi_broker_primary_contract == 1
        assert report.credential_schema_contract == 1

    def test_primary_provider_from_connections(self):
        report = DoctorReport.from_json(load_fixture(
            "stocklens_doctor.json"))
        assert report.primary_broker_provider == "kis"

    def test_old_doctor_has_no_new_capabilities(self):
        payload = load_fixture("stocklens_doctor.json")
        payload["capabilities"] = {"broker_connection_contract": 1,
                                   "market_data_router_contract": 1}
        report = DoctorReport.from_json(payload)
        assert report.broker_provider_registry_contract is None
        # v1 KIS 호환은 유지된다.
        assert orchestrator.broker_supported(STOCKLENS, report)


class TestV1KisCompat:
    def test_kis_verify_request_is_unchanged(self):
        _, mock_run = _run(
            "verify_and_save",
            {"ok": True, "contract_version": 1,
             "action": "verify_and_save", "status": {}},
            provider="kis", profile="real",
            credentials={"app_key": "k", "app_secret": "s"})
        sent = json.loads(mock_run.call_args.kwargs["input_text"])
        assert sent["contract_version"] == 1
        assert sent["provider"] == "kis"
        assert sent["credentials"] == {"app_key": "k", "app_secret": "s"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

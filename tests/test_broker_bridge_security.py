"""Manager 브리지 보안 테스트 (1.0 Task 21).

- 발급 URL 은 로컬 고정 표만 쓴다 (JS 가 임의 URL 을 열 수 없다)
- credential 은 provider descriptor 필드명 그대로, stdin 으로만 흐른다
- 성공·실패·예외 모두에서 브리지가 받은 credential dict 를 비운다
- set_primary_provider / describe_providers 중계
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from leetkit_manager.models import BrokerActionResult
from leetkit_manager.ui import api as api_module

SENTINEL_SECRET = "PSA-M21-SECRET-13579"


def _ok_result(action="verify_and_save"):
    return BrokerActionResult(
        ok=True, action=action, status={"active_profile": "real"})


def _fail_result():
    return BrokerActionResult(
        ok=False, error_code="credential_invalid", message="인증 실패")


class TestSignupUrls:
    def test_all_three_providers_have_fixed_official_urls(self):
        urls = api_module.BROKER_SIGNUP_URLS
        assert urls["kis"].startswith("https://apiportal.koreainvestment")
        assert urls["kiwoom"].startswith("https://openapi.kiwoom.com")
        assert urls["toss"].startswith("https://")
        assert "tossinvest" in urls["toss"]

    def test_unknown_provider_never_opens_browser(self):
        api = api_module.Api()
        with patch.object(api_module.webbrowser, "open") as opened:
            api.open_broker_signup("javascript:alert(1)")
            api.open_broker_signup("https://evil.example.com")
        opened.assert_not_called()

    def test_known_provider_opens_only_fixed_url(self):
        api = api_module.Api()
        with patch.object(api_module.webbrowser, "open") as opened:
            api.open_broker_signup("kiwoom")
        opened.assert_called_once_with(
            api_module.BROKER_SIGNUP_URLS["kiwoom"])


class TestGenericConnect:
    def _connect(self, result=None, side_effect=None,
                 credentials=None):
        api = api_module.Api()
        creds = credentials if credentials is not None else {
            "client_id": "cid", "client_secret": SENTINEL_SECRET}
        kwargs = {}
        if side_effect is not None:
            kwargs["side_effect"] = side_effect
        else:
            kwargs["return_value"] = result or _ok_result()
        with patch.dict(
                "os.environ", {"LEETKIT_ENABLE_EXPERIMENTAL_BROKERS": "1"}), \
             patch.object(api_module.orchestrator, "broker_action",
                          **kwargs) as mock_action, \
             patch.object(api, "diagnose_one", return_value={}):
            out = api.broker_connect(
                "stocklens", "real", creds, provider="toss")
        return out, creds, mock_action

    def test_credentials_pass_through_and_cleared_on_success(self):
        out, creds, mock_action = self._connect()
        assert out["ok"]
        sent = mock_action.call_args.kwargs["credentials"]
        assert sent is not creds  # 원본 참조를 넘기지 않는다
        assert creds == {}  # 브리지가 받은 dict 는 비워졌다

    def test_credentials_cleared_on_failure(self):
        out, creds, _ = self._connect(result=_fail_result())
        assert not out["ok"]
        assert creds == {}
        assert SENTINEL_SECRET not in str(out)

    def test_credentials_cleared_on_exception(self):
        with pytest.raises(RuntimeError):
            self._connect(side_effect=RuntimeError("boom"))
        # 예외 경로 확인: 별도 호출로 재현
        api = api_module.Api()
        creds = {"client_id": "cid", "client_secret": SENTINEL_SECRET}
        with patch.dict(
                "os.environ", {"LEETKIT_ENABLE_EXPERIMENTAL_BROKERS": "1"}), \
             patch.object(api_module.orchestrator, "broker_action",
                          side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                api.broker_connect("stocklens", "real", creds,
                                   provider="toss")
        assert creds == {}

    def test_error_output_never_contains_secret(self):
        out, _, _ = self._connect(result=_fail_result())
        assert SENTINEL_SECRET not in str(out)


class TestNewRelays:
    def test_set_primary_relay(self):
        api = api_module.Api()
        with patch.object(api_module.orchestrator, "broker_action",
                          return_value=_ok_result(
                              "set_primary_provider")) as mock_action, \
             patch.object(api, "diagnose_one", return_value={}):
            out = api.broker_set_primary("stocklens", provider="kiwoom")
        assert out["ok"]
        assert mock_action.call_args.args[1] == "set_primary_provider"
        assert mock_action.call_args.kwargs["provider"] == "kiwoom"

    def test_describe_providers_passthrough_and_legacy_none(self):
        api = api_module.Api()
        providers = [{"provider_id": "kis", "credential_fields": []}]
        with patch.object(api_module.orchestrator,
                          "broker_describe_providers",
                          return_value=providers):
            out = api.broker_describe_providers("stocklens")
        assert out["providers"] == providers

        with patch.object(api_module.orchestrator,
                          "broker_describe_providers",
                          return_value=None):
            out = api.broker_describe_providers("stocklens")
        assert out["providers"] is None  # 구 StockLens: KIS 전용 동작


class TestExperimentalBrokerGate:
    """고객 모드(플래그 없음)의 실험 공급자 경계 (2026-08-28 리뷰 반영).

    신규 노출 경로(상태·연결·주 사용·발급 URL)는 막고, 기존 자격 증명
    정리(해제·복구)는 StockLens CLI 계약과 동일하게 통과시킨다.
    """

    def _no_flag(self):
        import os

        env = dict(os.environ)
        env.pop("LEETKIT_ENABLE_EXPERIMENTAL_BROKERS", None)
        return patch.dict("os.environ", env, clear=True)

    def test_customer_mode_blocks_new_toss_actions(self):
        api = api_module.Api()
        with self._no_flag(), \
             patch.object(api_module.orchestrator,
                          "broker_action") as mock_action:
            st = api.broker_status("stocklens", "toss")
            conn = api.broker_connect(
                "stocklens", "real",
                {"client_id": "cid", "client_secret": SENTINEL_SECRET},
                "toss")
            prim = api.broker_set_primary("stocklens", "toss")
        assert st["error_code"] == "provider_not_public"
        assert conn["error_code"] == "provider_not_public"
        assert prim["error_code"] == "provider_not_public"
        mock_action.assert_not_called()

    def test_customer_mode_allows_hidden_cleanup(self):
        # 숨김이 정리까지 막으면 "MCP 삭제 없이 증권사별 연결·캐시 정리"
        # 요구와 충돌한다. 해제·복구는 CLI 로 그대로 통과해야 한다.
        api = api_module.Api()
        with self._no_flag(), \
             patch.object(api_module.orchestrator, "broker_action",
                          return_value=_ok_result()) as mock_action, \
             patch.object(api, "diagnose_one", return_value={}):
            a = api.broker_disconnect_profile("stocklens", "real", "toss")
            b = api.broker_disconnect_provider("stocklens", "toss")
            c = api.broker_recover("stocklens", "toss")
        assert a["ok"] and b["ok"] and c["ok"]
        assert mock_action.call_count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

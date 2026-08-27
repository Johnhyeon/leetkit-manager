"""증권사 연결 UI 계약 테스트 (Task 16).

- 라이선스 활성화와 분리된 진입점
- real 기본 선택, demo 경고 문구
- App Key·App Secret 입력, 저장 전 연결 시험
- 국내·미국 능력 개별 표시, 데이터 모드 3종
- 연결 시험·환경 변경·현재 환경 해제·전체 해제
- 구 StockLens 는 업데이트 필요 상태
- 변경 API 는 broker_action 경유 + 재진단, 비밀값 무노출
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from leetkit_manager.models import BrokerActionResult
from leetkit_manager.ui import api as api_module

UI = Path(__file__).resolve().parent.parent / "leetkit_manager" / "ui"
HTML = (UI / "index.html").read_text(encoding="utf-8")
JS = (UI / "app.js").read_text(encoding="utf-8")

SENTINEL_KEY = "PSA-SENTINEL-APP-KEY-UI16"
SENTINEL_SECRET = "PSA-SENTINEL-APP-SECRET-UI16"


class TestHtmlContract:
    def test_broker_entry_separate_from_license(self):
        # 카드 렌더러에 증권사 연결 진입점이 있고, 활성화 버튼과 별개다.
        assert "증권사 연결" in JS
        assert 'data-action="broker"' in JS
        assert 'data-action="activate"' in JS

    def test_modal_has_credential_fields(self):
        assert 'id="broker-appkey"' in HTML
        assert 'id="broker-appsecret"' in HTML
        # 비밀 입력은 화면에 노출되지 않는 password 타입이어야 한다.
        secret_input = re.search(
            r'<input[^>]*id="broker-appsecret"[^>]*>', HTML).group(0)
        assert 'type="password"' in secret_input

    def test_real_profile_is_default(self):
        real = re.search(
            r'<input[^>]*name="broker-profile"[^>]*value="real"[^>]*>',
            HTML).group(0)
        assert "checked" in real
        demo = re.search(
            r'<input[^>]*name="broker-profile"[^>]*value="demo"[^>]*>',
            HTML).group(0)
        assert "checked" not in demo

    def test_demo_warning_text(self):
        assert "모의" in HTML
        assert re.search(r"모의[^<]*분봉", HTML) or "과거 분봉" in HTML

    def test_data_source_modes(self):
        for value in ("auto", "broker_first", "legacy"):
            assert f'value="{value}"' in HTML
        assert "자동" in HTML
        assert "증권사 우선" in HTML
        assert "기본 데이터" in HTML

    def test_capability_rows_shown_separately(self):
        assert 'id="broker-cap-kr"' in HTML
        assert 'id="broker-cap-us"' in HTML
        assert "국내 분봉" in HTML
        assert "미국 분봉" in HTML

    def test_action_buttons(self):
        assert 'id="broker-connect-btn"' in HTML
        assert 'id="broker-switch-btn"' in HTML
        assert 'id="broker-disconnect-profile-btn"' in HTML
        assert 'id="broker-disconnect-provider-btn"' in HTML
        assert "연결 시험" in HTML
        assert "현재 환경 연결 해제" in HTML
        assert "모두 해제" in HTML

    def test_change_key_button_exists(self):
        # 연결된 상태에서는 키를 다시 입력할 필요가 없다. 키 교체는
        # [키 변경]으로만 입력 폼을 연다.
        assert 'id="broker-change-key-btn"' in HTML
        assert "키 변경" in HTML

    def test_mode_has_explicit_save_button(self):
        # 사용 방식 라디오는 클릭만으로 저장되지 않는다. [적용] 버튼 필수.
        assert 'id="broker-mode-save-btn"' in HTML

    def test_provider_selector_exists(self):
        # 다른 증권사 추가를 고려한 구조. 지금은 한국투자증권 하나다.
        assert 'id="broker-provider"' in HTML
        assert 'value="kis"' in HTML
        assert "한국투자증권" in HTML

    def test_signup_help_and_url_button(self):
        # DART API 키 모달처럼 발급 절차 안내 + 포털 열기 버튼을 함께 둔다.
        assert 'id="broker-signup-btn"' in HTML
        assert 'id="broker-signup-steps"' in HTML
        assert "API 포털" in HTML


class TestJsContract:
    def test_broker_button_only_for_stocklens(self):
        # renderCard 에서 stocklens 조건으로만 버튼을 만든다.
        m = re.search(r"lens\.name === \"stocklens\"[^;]*증권사 연결",
                      JS, re.S)
        assert m, "stocklens 카드에만 증권사 연결 버튼이 있어야 합니다"

    def test_state_machine_states(self):
        for state in ("not_supported", "not_configured", "verifying",
                      "connected", "limited", "reauth_required",
                      "disconnecting", "error"):
            assert f'"{state}"' in JS, f"BROKER state {state} 누락"

    def test_update_required_copy(self):
        assert "update_required" in JS
        assert "업데이트" in JS

    def test_success_clears_inputs(self):
        # 성공 시 입력칸을 비운다 (비밀값을 화면에 남기지 않는다).
        m = re.search(r"broker-appkey.*?value = \"\"", JS, re.S) or \
            re.search(r"clearBrokerInputs", JS)
        assert m

    def test_connected_state_hides_key_form_until_change_key(self):
        # 연결됨 상태의 폼 표시는 '키 변경' 편집 플래그로만 열린다.
        assert "brokerEditingKey" in JS
        assert re.search(r"broker-change-key-btn", JS)

    def test_mode_radio_change_does_not_call_api(self):
        # 라디오 change 핸들러 블록에는 API 호출이 없고, 저장은 [적용]
        # 버튼 핸들러에서만 일어난다.
        radio_start = JS.index("input[name=\"broker-mode\"]').forEach")
        save_start = JS.index('broker-mode-save-btn").addEventListener')
        radio_block = JS[radio_start:save_start]
        assert "broker_set_mode" not in radio_block
        save_block = JS[save_start:save_start + 1200]
        assert "broker_set_mode" in save_block

    def test_provider_flows_through_js_calls(self):
        # API 호출에 provider 가 인자로 흐른다 (다중 증권사 대비).
        assert re.search(r"broker_connect\([^)]*brokerProvider", JS) or \
            re.search(r"brokerProvider", JS)

    def test_first_time_glow_until_opened(self):
        # 새로 생긴 버튼은 처음 열 때까지 반짝인다. localStorage 로 1회 관리.
        assert "BROKER_INTRO_KEY" in JS
        assert "new-glow" in JS
        css = (UI / "style.css").read_text(encoding="utf-8")
        assert "new-glow" in css

    def test_tour_includes_broker_guide(self):
        # 상단 가이드에 증권사 연결 단계가 있고, 모달을 예시로 띄우는
        # demo 훅으로 버튼·입력칸을 하나하나 가리킨다.
        tour_start = JS.index("const TOUR_STEPS")
        tour_block = JS[tour_start:tour_start + 30000]
        assert "증권사 연결" in tour_block
        assert '"broker"' in tour_block
        for target in ("#broker-provider", "#broker-appkey",
                       "#broker-connect-btn", "#broker-mode-field",
                       "#broker-signup-btn", "#broker-manage-row",
                       "#broker-disconnect-provider-btn"):
            assert target in tour_block, f"가이드에 {target} 단계 누락"

    def test_demo_mode_blocks_real_actions(self):
        assert "brokerDemoMode" in JS

    def test_status_unavailable_shown_not_swallowed(self):
        # 성공 토스트가 status_unavailable 경고를 삼키지 않는다.
        assert "status_unavailable" in JS
        assert "brokerSuccessNotice" in JS
        # 연결 성공 경로가 헬퍼를 쓴다 (경고 미반영 토스트 금지).
        connect_start = JS.index('broker-connect-btn").addEventListener')
        connect_block = JS[connect_start:connect_start + 3000]
        assert "brokerSuccessNotice" in connect_block


class FakeDiag:
    def __init__(self):
        self.report = None

    def to_dict(self):
        return {"name": "stocklens"}


class TestApiContract:
    def _api(self):
        return api_module.Api()

    def _ok_result(self, action, **extra):
        payload = {
            "ok": True, "contract_version": 1, "action": action,
            "status": {
                "provider": "kis", "active_provider": "kis",
                "active_profile": "real", "data_source_mode": "auto",
                "connection_generation": 3,
                "profiles": {"real": {"configured": True},
                             "demo": {"configured": False}},
            },
        }
        payload.update(extra)
        return BrokerActionResult.from_json(payload, exit_code=0)

    def test_connect_uses_verify_and_save_and_rediagnoses(self):
        api = self._api()
        with patch.object(api_module.orchestrator, "broker_action",
                          return_value=self._ok_result(
                              "verify_and_save",
                              verification={"auth": "ok",
                                            "kr_intraday": "available",
                                            "us_intraday": "unavailable"}),
                          ) as mock_action, \
             patch.object(api, "diagnose_one",
                          return_value={"name": "stocklens"}) as mock_diag:
            result = api.broker_connect(
                "stocklens", "real", SENTINEL_KEY, SENTINEL_SECRET)

        assert result["ok"]
        assert mock_action.call_args.args[1] == "verify_and_save"
        assert mock_action.call_args.kwargs["profile"] == "real"
        assert mock_action.call_args.kwargs["credentials"]["app_key"] == \
            SENTINEL_KEY
        mock_diag.assert_called_once()
        assert result["verification"]["kr_intraday"] == "available"
        assert result["verification"]["us_intraday"] == "unavailable"
        # 반환값 어디에도 비밀 원문이 없다.
        assert SENTINEL_KEY not in json.dumps(result, ensure_ascii=False)
        assert SENTINEL_SECRET not in json.dumps(result, ensure_ascii=False)

    def test_connect_failure_keeps_state_no_rediagnosis_needed(self):
        api = self._api()
        failed = BrokerActionResult.from_json({
            "ok": False,
            "error": {"code": "credential_invalid",
                      "message": "인증 실패"}}, exit_code=1)
        with patch.object(api_module.orchestrator, "broker_action",
                          return_value=failed), \
             patch.object(api, "diagnose_one") as mock_diag:
            result = api.broker_connect("stocklens", "real", "k", "s")
        assert not result["ok"]
        assert result["error_code"] == "credential_invalid"
        mock_diag.assert_not_called()

    @pytest.mark.parametrize("method,action,kwargs", [
        ("broker_switch_profile", "switch_profile", {"profile": "demo"}),
        ("broker_disconnect_profile", "disconnect_profile",
         {"profile": "real"}),
        ("broker_disconnect_provider", "disconnect_provider", {}),
        ("broker_set_mode", "set_data_source_mode", {"mode": "legacy"}),
    ])
    def test_mutating_methods_route_and_rediagnose(self, method, action,
                                                   kwargs):
        api = self._api()
        with patch.object(api_module.orchestrator, "broker_action",
                          return_value=self._ok_result(action)) as mock_a, \
             patch.object(api, "diagnose_one",
                          return_value={"name": "stocklens"}) as mock_diag:
            result = getattr(api, method)("stocklens", *kwargs.values())
        assert result["ok"]
        assert mock_a.call_args.args[1] == action
        mock_diag.assert_called_once()

    def test_status_reports_support_and_connection(self):
        api = self._api()
        with patch.object(api_module.orchestrator, "broker_action",
                          return_value=self._ok_result("status")):
            result = api.broker_status("stocklens")
        assert result["supported"]
        assert result["status"]["active_profile"] == "real"

    def test_status_update_required_for_old_stocklens(self):
        api = self._api()
        old = BrokerActionResult.from_json({
            "ok": False,
            "error": {"code": "update_required",
                      "message": "업데이트가 필요합니다"}}, exit_code=1)
        with patch.object(api_module.orchestrator, "broker_action",
                          return_value=old):
            result = api.broker_status("stocklens")
        assert not result["supported"]
        assert result["error_code"] == "update_required"

    def test_non_stocklens_is_not_supported(self):
        api = self._api()
        result = api.broker_status("dartlens")
        assert not result["supported"]

    def test_provider_param_flows_to_orchestrator(self):
        # 다른 증권사 추가 대비: API 는 provider 를 orchestrator 로 흘린다.
        api = self._api()
        with patch.object(api_module.orchestrator, "broker_action",
                          return_value=self._ok_result("status")) as mock_a:
            api.broker_status("stocklens", provider="kis")
        assert mock_a.call_args.kwargs["provider"] == "kis"

        with patch.object(api_module.orchestrator, "broker_action",
                          return_value=self._ok_result(
                              "verify_and_save")) as mock_a, \
             patch.object(api, "diagnose_one", return_value={}):
            api.broker_connect("stocklens", "real", "k", "s",
                               provider="kis")
        assert mock_a.call_args.kwargs["provider"] == "kis"

    def test_unknown_provider_is_not_supported(self):
        api = self._api()
        result = api.broker_status("stocklens", provider="mirae")
        assert not result["supported"]
        assert result["error_code"] == "provider_unsupported"

    def test_status_unavailable_and_warnings_reach_api_response(self):
        # 리뷰 지적: _broker_result 가 status_unavailable·warnings 를
        # 버려서 UI 가 경고 없이 "연결 완료"를 띄웠다.
        api = self._api()
        result = BrokerActionResult.from_json({
            "ok": True, "contract_version": 1, "action": "verify_and_save",
            "status": {"active_profile": "real"},
            "status_unavailable": True,
            "warnings": ["변경은 저장됐지만 상태 재조회에 실패했습니다"],
            "verification": {"auth": "ok", "kr_intraday": "available",
                             "us_intraday": "available"},
        }, exit_code=0)
        with patch.object(api_module.orchestrator, "broker_action",
                          return_value=result), \
             patch.object(api, "diagnose_one", return_value={}):
            out = api.broker_connect("stocklens", "real", "k", "s")
        assert out["ok"]
        assert out["status_unavailable"] is True
        assert any("재조회" in w for w in out["warnings"])

    def test_open_broker_signup_opens_portal(self):
        api = self._api()
        with patch.object(api_module.webbrowser, "open") as mock_open:
            api.open_broker_signup("kis")
        opened = mock_open.call_args.args[0]
        assert "apiportal.koreainvestment.com" in opened

    def test_open_broker_signup_unknown_provider_noop(self):
        api = self._api()
        with patch.object(api_module.webbrowser, "open") as mock_open:
            api.open_broker_signup("mirae")
        mock_open.assert_not_called()

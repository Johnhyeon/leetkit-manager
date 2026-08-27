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
        # 1.0: 사용자에게는 두 가지만 보여준다. broker_first 는 내부
        # 호환용으로 JS 에만 남는다 (노출 금지).
        for value in ("auto", "legacy"):
            assert f'value="{value}"' in HTML
        assert 'value="broker_first"' not in HTML
        assert "일반 사용" in HTML
        assert "권장" in HTML
        assert "기본 데이터만 사용" in HTML
        assert "broker_first" in JS  # 저장된 구 설정 호환

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
        assert 'id="broker-provider"' in HTML
        assert 'value="kis"' in HTML
        assert "한국투자증권" in HTML

    def test_all_three_providers_selectable(self):
        # 1.0 멀티 증권사: 세 공급자 탭. 구 StockLens 연결 시 JS 가
        # kiwoom·toss 탭을 숨긴다.
        for value, label in (("kiwoom", "키움증권"), ("toss", "토스증권")):
            assert f'value="{value}"' in HTML, value
            assert label in HTML, label

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


class TestSinglePrimaryUx:
    """1.0 Task 22: 하나의 주 사용 증권사 UX."""

    def test_required_copy_present(self):
        text = HTML + JS
        for phrase in (
            "증권사 하나만 연결하면 됩니다",
            "여러 증권사 연결은 선택 사항입니다",
            "사용 중",
            "연결됨",
            "연결 안 됨, 선택 사항",
            "데이터 출처를 자동으로 변경하지 않았습니다",
        ):
            assert phrase in text, phrase

    def test_set_primary_button_and_relay(self):
        assert 'id="broker-set-primary-btn"' in HTML
        assert "주 사용으로 변경" in HTML + JS
        assert "broker_set_primary" in JS

    def test_descriptor_driven_credential_fields(self):
        # 자격 증명 입력은 descriptor 의 credential_fields 로 만든다.
        assert 'id="broker-credential-fields"' in HTML
        assert "credential_fields" in JS
        # 동적으로 만든 입력도 password + autocomplete off 다.
        assert re.search(
            r"createElement\(\"input\"\)[\s\S]{0,400}type\s*=\s*\"password\"",
            JS)
        assert re.search(
            r"createElement\(\"input\"\)[\s\S]{0,600}autocomplete", JS)

    def test_describe_providers_used_with_legacy_fallback(self):
        assert "broker_describe_providers" in JS
        # 구 StockLens(providers=None)면 KIS 전용으로 동작한다.
        assert "KIS_FALLBACK" in JS or "kisFallback" in JS

    def test_no_demo_assumption_per_provider(self):
        # supported_profiles 에 demo 가 없으면(토스) 모의 선택지를 숨긴다.
        assert "supported_profiles" in JS
        assert re.search(r"supported_profiles[\s\S]{0,400}demo", JS)

    def test_provider_error_messages_use_textcontent(self):
        # provider 오류 문구는 textContent 로만 그린다 (innerHTML 금지).
        broker_start = JS.index("const BROKER_STATES")
        broker_end = JS.index("/* ---------- 활성화 모달")
        broker_block = JS[broker_start:broker_end]
        assert "innerHTML" not in broker_block

    def test_no_credential_localstorage(self):
        for match in re.finditer(r"localStorage\.setItem\(([^)]*)\)", JS):
            args = match.group(1)
            assert "BROKER_INTRO_KEY" in args or "secret" not in args.lower()
            assert "appkey" not in args.lower()
            assert "credential" not in args.lower()

    def test_inputs_cleared_on_cancel_and_close(self):
        cancel_start = JS.index('broker-cancel").addEventListener')
        cancel_block = JS[cancel_start:cancel_start + 800]
        assert "clearBrokerInputs" in cancel_block

    def test_primary_disconnect_never_auto_switches(self):
        # 주 사용 해제 후 다른 증권사·Yahoo 로 자동 전환하지 않는다.
        assert "데이터 출처를 자동으로 변경하지 않았습니다" in JS

    def test_active_profile_is_per_provider_not_primary(self):
        # 재현된 결함(리뷰): 주 사용 KIS=모의 + 토스=실전 연결 상태에서
        # 토스 탭이 KIS 의 demo 를 기준으로 판단해 토스를 미연결로 표시.
        # 활성 프로필은 반드시 선택된 증권사(status.providers[provider])
        # 에서 읽어야 한다. top-level active_profile 은 primary 호환
        # 필드일 뿐이다.
        assert "brokerActiveProfile" in JS
        assert re.search(
            r"function brokerActiveProfile[\s\S]{0,400}"
            r"providers[\s\S]{0,40}\[brokerProvider\]", JS)
        # renderBrokerModal 과 전환·해제 핸들러가 helper 를 쓴다.
        render_start = JS.index("function renderBrokerModal")
        render_block = JS[render_start:render_start + 4000]
        assert "brokerActiveProfile" in render_block
        assert re.search(r"const active = status\.active_profile;",
                         JS) is None


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
        captured: dict = {}

        def fake_action(lens, action, **kwargs):
            # 브리지는 호출 뒤 버퍼를 비우므로(1.0 Task 21) 호출 시점에
            # 복사해 검증한다.
            captured["action"] = action
            captured["profile"] = kwargs.get("profile")
            captured["credentials"] = dict(kwargs.get("credentials") or {})
            return self._ok_result(
                "verify_and_save",
                verification={"auth": "ok", "kr_intraday": "available",
                              "us_intraday": "unavailable"})

        with patch.object(api_module.orchestrator, "broker_action",
                          side_effect=fake_action), \
             patch.object(api, "diagnose_one",
                          return_value={"name": "stocklens"}) as mock_diag:
            result = api.broker_connect(
                "stocklens", "real",
                {"app_key": SENTINEL_KEY, "app_secret": SENTINEL_SECRET})

        assert result["ok"]
        assert captured["action"] == "verify_and_save"
        assert captured["profile"] == "real"
        assert captured["credentials"]["app_key"] == SENTINEL_KEY
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
            result = api.broker_connect(
                "stocklens", "real", {"app_key": "k", "app_secret": "s"})
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
            api.broker_connect(
                "stocklens", "real",
                {"app_key": "k", "app_secret": "s"}, provider="kis")
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
            out = api.broker_connect(
                "stocklens", "real", {"app_key": "k", "app_secret": "s"})
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

"""Manager 멀티 증권사 E2E 게이트 (1.0 Task 23). 전부 mock 기반.

- 프로그램만 삭제는 어떤 공급자의 자격 증명도 건드리지 않는다
- 완전 정리는 세 공급자를 모두 해제한 뒤에만 패키지·라이선스로 간다
- 중간 공급자 해제 실패는 즉시 중단 (조용한 계속 금지)
- Manager 는 어떤 흐름에서도 primary 를 임의로 바꾸지 않는다
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from leetkit_manager.models import BrokerActionResult
from leetkit_manager.ui import api as api_module


class FakeUninstallResult:
    def __init__(self):
        self.ok = True
        self.error = None
        self.license_removed = True
        self.uninstall = type("P", (), {
            "stderr": "", "stdout": "", "exit_code": 0, "error": None,
            "ok": True})()


def _ok_broker(action="disconnect_provider"):
    return BrokerActionResult(ok=True, action=action, status={})


class TestProgramDeleteKeepsAllProviders:
    def test_uninstall_never_touches_any_provider(self):
        api = api_module.Api()
        with patch.object(api_module.orchestrator, "uninstall_lens",
                          return_value=FakeUninstallResult()), \
             patch.object(api_module.orchestrator,
                          "broker_action") as broker:
            api.uninstall("stocklens", remove_license=False)
        broker.assert_not_called()


class TestFullCleanupMatrix:
    def _run(self, results_by_provider):
        api = api_module.Api()
        calls: list[tuple[str, str]] = []

        def fake_broker(lens, action, provider="kis", **kwargs):
            calls.append((action, provider))
            return results_by_provider.get(provider, _ok_broker())

        with patch.object(api_module.orchestrator, "broker_action",
                          side_effect=fake_broker), \
             patch.object(api, "register",
                          return_value={"ok": True, "error": None,
                                        "removed": []}), \
             patch.object(api_module.orchestrator, "uninstall_lens",
                          return_value=FakeUninstallResult()):
            out = api.full_cleanup("stocklens")
        return out, calls

    def test_all_three_disconnected_before_package_removal(self):
        out, calls = self._run({})
        assert out["ok"]
        assert [c for c in calls if c[0] == "disconnect_provider"] == [
            ("disconnect_provider", "kis"),
            ("disconnect_provider", "kiwoom"),
            ("disconnect_provider", "toss"),
        ]

    def test_middle_provider_failure_stops_everything(self):
        failing = BrokerActionResult(
            ok=False, error_code="keychain_unavailable",
            message="자격 증명 일부를 삭제할 수 없습니다")
        out, calls = self._run({"kiwoom": failing})
        assert not out["ok"]
        assert out["stage"] == "broker"
        # kiwoom 에서 멈추고 toss 로 넘어가지 않는다.
        providers = [p for a, p in calls if a == "disconnect_provider"]
        assert providers == ["kis", "kiwoom"]
        assert "남아" in out["error"]

    def test_old_stocklens_without_broker_command_continues(self):
        old = BrokerActionResult(
            ok=False, error_code="update_required", message="업데이트 필요")
        out, _ = self._run({"kis": old, "kiwoom": old, "toss": old})
        assert out["ok"]
        assert out["broker_cleanup_ok"] is None


class TestNoImplicitPrimaryChange:
    """Manager UI 흐름 어디에서도 set_primary_provider 를 임의로 보내지
    않는다. primary 변경은 전용 버튼(broker_set_primary)뿐이다."""

    def _record_calls(self, fn):
        api = api_module.Api()
        calls: list[str] = []

        def fake_broker(lens, action, **kwargs):
            calls.append(action)
            return _ok_broker(action)

        with patch.object(api_module.orchestrator, "broker_action",
                          side_effect=fake_broker), \
             patch.object(api, "diagnose_one", return_value={}):
            fn(api)
        return calls

    def test_connect_switch_disconnect_send_no_primary_change(self):
        calls = self._record_calls(lambda api: (
            api.broker_connect("stocklens", "real",
                               {"app_key": "k", "app_secret": "s"}),
            api.broker_switch_profile("stocklens", "demo"),
            api.broker_disconnect_profile("stocklens", "real"),
            api.broker_disconnect_provider("stocklens", "kiwoom"),
            api.broker_set_mode("stocklens", "auto"),
        ))
        assert "set_primary_provider" not in calls

    def test_dedicated_button_is_the_only_primary_path(self):
        calls = self._record_calls(
            lambda api: api.broker_set_primary("stocklens",
                                               provider="toss"))
        assert calls == ["set_primary_provider"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

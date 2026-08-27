"""연결 해제와 삭제의 경계 테스트 (Task 17).

네 가지 경로가 서로를 침범하지 않는다:
1. 현재 환경 해제: 패키지·라이선스·MCP·다른 프로필·과거 캐시 유지
2. 공급자 전체 해제: 패키지·라이선스·MCP 유지, KIS 캐시만 삭제(CLI 책임)
3. 프로그램만 삭제: broker 프로필 유지 (disconnect 호출 없음)
4. 완전 정리: broker 해제 -> MCP 해제 -> 패키지 삭제 -> 라이선스 삭제 순서
   실패를 성공으로 말하지 않는다
"""

from __future__ import annotations

from unittest.mock import patch

from leetkit_manager.models import BrokerActionResult
from leetkit_manager.ui import api as api_module


def _ok_broker(action="disconnect_provider"):
    return BrokerActionResult.from_json({
        "ok": True, "contract_version": 1, "action": action,
        "status": {"active_profile": None, "profiles": {
            "real": {"configured": False}, "demo": {"configured": False}}},
        "cache_removed": True,
    }, exit_code=0)


def _failed_broker(code="internal_error", message="실패"):
    return BrokerActionResult.from_json({
        "ok": False, "error": {"code": code, "message": message}},
        exit_code=1)


class FakeUninstallResult:
    def __init__(self, ok=True, license_removed=True):
        self.ok = ok
        self.license_removed = license_removed
        self.uninstall = type("P", (), {
            "stderr": "", "stdout": "", "exit_code": 0, "error": None,
            "ok": True})()


class TestDisconnectNeverUninstalls:
    def test_profile_disconnect_calls_nothing_destructive(self):
        api = api_module.Api()
        with patch.object(api_module.orchestrator, "broker_action",
                          return_value=_ok_broker("disconnect_profile")), \
             patch.object(api, "diagnose_one", return_value={}), \
             patch.object(api_module.orchestrator, "uninstall_lens") as un, \
             patch.object(api_module.orchestrator, "unregister_lens") as ur:
            result = api.broker_disconnect_profile("stocklens", "real")
        assert result["ok"]
        un.assert_not_called()
        ur.assert_not_called()

    def test_provider_disconnect_calls_nothing_destructive(self):
        api = api_module.Api()
        with patch.object(api_module.orchestrator, "broker_action",
                          return_value=_ok_broker()), \
             patch.object(api, "diagnose_one", return_value={}), \
             patch.object(api_module.orchestrator, "uninstall_lens") as un, \
             patch.object(api_module.orchestrator, "unregister_lens") as ur:
            result = api.broker_disconnect_provider("stocklens")
        assert result["ok"]
        un.assert_not_called()
        ur.assert_not_called()


class TestProgramOnlyUninstallPreservesProfiles:
    def test_uninstall_never_calls_broker(self):
        api = api_module.Api()
        with patch.object(api_module.orchestrator, "uninstall_lens",
                          return_value=FakeUninstallResult()), \
             patch.object(api_module.orchestrator,
                          "broker_action") as broker:
            api.uninstall("stocklens", remove_license=False)
        broker.assert_not_called()


class TestFullCleanupOrder:
    def _api_with_recorder(self, *, broker=None, register_ok=True,
                           uninstall=None):
        api = api_module.Api()
        calls: list[str] = []

        def fake_broker(lens, action, **kwargs):
            calls.append(f"broker:{action}")
            return broker if broker is not None else _ok_broker()

        def fake_register(lens_name, targets):
            calls.append(f"register:{targets}")
            return ({"ok": True, "error": None, "removed": ["claude-code"]}
                    if register_ok
                    else {"ok": False, "error": "실패", "removed": []})

        def fake_uninstall(lens, *, remove_license=False):
            calls.append(f"uninstall:remove_license={remove_license}")
            return uninstall if uninstall is not None else \
                FakeUninstallResult()

        patches = [
            patch.object(api_module.orchestrator, "broker_action",
                         side_effect=fake_broker),
            patch.object(api, "register", side_effect=fake_register),
            patch.object(api_module.orchestrator, "uninstall_lens",
                         side_effect=fake_uninstall),
        ]
        return api, calls, patches

    def test_order_broker_then_unregister_then_uninstall(self):
        api, calls, patches = self._api_with_recorder()
        with patches[0], patches[1], patches[2]:
            result = api.full_cleanup("stocklens")
        assert result["ok"]
        assert calls == [
            "broker:disconnect_provider",
            "register:[]",
            "uninstall:remove_license=True",
        ]
        assert result["broker_cleanup_attempted"] is True
        assert result["broker_cleanup_ok"] is True
        assert result["license_removed"] is True

    def test_broker_failure_stops_before_unregister(self):
        api, calls, patches = self._api_with_recorder(
            broker=_failed_broker())
        with patches[0], patches[1], patches[2]:
            result = api.full_cleanup("stocklens")
        assert not result["ok"]
        assert result["stage"] == "broker"
        assert result["broker_cleanup_ok"] is False
        # 자격 증명이 남았다는 사실을 말한다. 지웠다고 말하지 않는다.
        assert "남아" in result["error"]
        assert calls == ["broker:disconnect_provider"]

    def test_old_stocklens_update_required_proceeds(self):
        # 구 StockLens 는 broker 명령 자체가 없다 = 저장된 프로필도 없다.
        api, calls, patches = self._api_with_recorder(
            broker=_failed_broker(code="update_required",
                                  message="업데이트가 필요합니다"))
        with patches[0], patches[1], patches[2]:
            result = api.full_cleanup("stocklens")
        assert result["ok"]
        assert calls[-1] == "uninstall:remove_license=True"

    def test_unregister_failure_stops_without_force(self):
        api, calls, patches = self._api_with_recorder(register_ok=False)
        with patches[0], patches[1], patches[2]:
            result = api.full_cleanup("stocklens")
        assert not result["ok"]
        assert result["stage"] == "unregister"
        assert not any(c.startswith("uninstall") for c in calls)

    def test_unregister_failure_force_continues(self):
        api, calls, patches = self._api_with_recorder(register_ok=False)
        with patches[0], patches[1], patches[2]:
            result = api.full_cleanup("stocklens", force=True)
        assert result["ok"]
        assert calls[-1] == "uninstall:remove_license=True"

    def test_non_broker_lens_skips_broker_stage(self):
        api, calls, patches = self._api_with_recorder()
        with patches[0], patches[1], patches[2]:
            result = api.full_cleanup("dartlens")
        assert result["ok"]
        assert result["broker_cleanup_attempted"] is False
        assert calls[0] == "register:[]"

    def test_cache_cleanup_failure_stops_with_accurate_message(self):
        # StockLens 가 cache_cleanup_failed 를 주면 자격 증명은 이미
        # 지워진 상태다 - "자격 증명이 남아 있다"고 말하면 틀린다.
        api, calls, patches = self._api_with_recorder(
            broker=_failed_broker(code="cache_cleanup_failed",
                                  message="자격 증명은 삭제됐지만 분봉 캐시 삭제에 실패했습니다"))
        with patches[0], patches[1], patches[2]:
            result = api.full_cleanup("stocklens")
        assert not result["ok"]
        assert result["stage"] == "broker"
        assert "캐시" in result["error"]
        assert "자격 증명이 이 컴퓨터에 남아" not in result["error"]
        assert calls == ["broker:disconnect_provider"]

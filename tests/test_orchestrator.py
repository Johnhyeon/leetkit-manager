"""orchestrator.py 단위 테스트.

실제 uv/글로벌 설치된 Lens CLI에 의존하지 않는다 — `run_json_cli`/`run_cli`를
monkeypatch해서 "이 JSON이 오면 orchestrator가 이렇게 해석한다"만 검증한다.
subprocess 자체(timeout/stdin 전달 등)는 test_process_runner.py가 이미 커버한다.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from conftest import load_fixture

from leetkit_manager import orchestrator
from leetkit_manager.lens_contract import DARTLENS, STOCKLENS, TELEGRAMLENS
from leetkit_manager.process_runner import ProcessResult


@pytest.fixture(autouse=True)
def _no_uv_bin_resolution():
    """이 머신에 실제로 uv tool bin 디렉터리·설치가 있으면 resolve_lens_command가
    bare 이름 대신 절대경로를 반환해 아래 cmd 문자열 비교가 깨진다 — 커맨드 이름
    해석 자체는 package_service 쪽 테스트가 따로 검증하므로 여기서는 항상 identity로
    고정해 orchestrator 로직만 결정론적으로 검증한다."""
    with patch("leetkit_manager.orchestrator.package_service.resolve_lens_command", side_effect=lambda name: name):
        yield


@pytest.fixture(autouse=True)
def _no_real_pypi_lookup():
    """diagnose_lens()가 update_available == null인 report에 대해 PyPI 조회
    폴백(_fill_update_info)을 시도하는데, 여기서 실제 네트워크를 타면 단위 테스트가
    느려지고 네트워크 상태에 따라 결과가 흔들린다 — 기본은 조회 실패(None)로 고정해
    기존 테스트들의 update_available 기대값(대부분 무관심)을 그대로 유지한다.
    폴백 동작 자체를 검증하는 테스트는 이 안에서 다시 patch해서 값을 준다."""
    with patch("leetkit_manager.orchestrator.package_service.latest_pypi_version", return_value=None):
        yield


def _fake_process(*, exit_code: int = 0, error: str | None = None) -> ProcessResult:
    return ProcessResult(
        cmd=["fake"], exit_code=exit_code, stdout="", stderr="",
        timed_out=(error == "timeout"), duration_s=0.01, error=error,
    )


class TestDiagnoseLens:
    def test_ok_report_parses_normally(self):
        payload = load_fixture("stocklens_doctor.json")
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(exit_code=1), payload)):
            diag = orchestrator.diagnose_lens(STOCKLENS)
        assert diag.not_installed is False
        assert diag.incompatible is False
        assert diag.readiness == "주의"

    def test_command_not_found_marks_not_installed(self):
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(error="not_found"), None)):
            diag = orchestrator.diagnose_lens(TELEGRAMLENS)
        assert diag.not_installed is True
        assert diag.readiness == "미설치"

    def test_unparseable_json_marks_incompatible(self):
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(exit_code=0), None)):
            diag = orchestrator.diagnose_lens(STOCKLENS)
        assert diag.incompatible is True
        assert diag.readiness == "호환되지 않는 Lens 버전"

    def test_non_dict_payload_degrades_instead_of_crashing_whole_diagnosis(self):
        """doctor가 dict가 아닌 유효 JSON(배열/문자열/숫자)을 뱉으면 payload.get에서
        AttributeError가 나 진단 전체가 죽었다 — 한 Lens의 계약 위반이 다른 Lens 카드까지
        같이 무너뜨리면 안 된다."""
        for bad_payload in ([], "문자열", 42, [None]):
            with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), bad_payload)):
                diag = orchestrator.diagnose_lens(STOCKLENS)
            assert diag.incompatible is True, f"payload={bad_payload!r}"
            assert diag.readiness == "호환되지 않는 Lens 버전"

    def test_malformed_inner_types_degrade_instead_of_crashing(self):
        """최상위는 dict인데 내부 타입이 계약과 다른 경우(license가 문자열 등)."""
        payload = {**load_fixture("stocklens_doctor.json"), "license": "active"}
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), payload)):
            diag = orchestrator.diagnose_lens(STOCKLENS)
        assert diag.incompatible is True

    def test_unsupported_schema_version_marks_incompatible(self):
        payload = {**load_fixture("stocklens_doctor.json"), "schema_version": 999}
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), payload)):
            diag = orchestrator.diagnose_lens(STOCKLENS)
        assert diag.incompatible is True

    def test_incompatible_lens_still_gets_update_available_filled(self):
        """실사용 중 발견된 문제 재현: "호환되지 않는 Lens 버전"이야말로 업데이트가
        정답인 경우인데, 예전엔 호환성 판정이 PyPI 조회보다 먼저 return해버려서
        update_available이 계속 null로 남아 카드에 업데이트 버튼 자체가 안 떴다."""
        payload = {**load_fixture("stocklens_doctor.json"), "schema_version": 999}
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), payload)), \
             patch("leetkit_manager.orchestrator.package_service.latest_pypi_version", return_value="9.9.9"):
            diag = orchestrator.diagnose_lens(STOCKLENS)
        assert diag.incompatible is True
        assert diag.report.latest_version == "9.9.9"
        assert diag.report.update_available is True

    def test_null_update_available_falls_back_to_pypi_lookup(self):
        """doctor가 --online 없이 update_available=null을 줬을 때(StockLens/DartLens
        기본 동작이자 TelegramLens는 항상 이 상태) — Manager가 PyPI를 직접 조회해서
        채워준다(그래야 대시보드에 업데이트가 실제로 뜬다)."""
        payload = load_fixture("stocklens_doctor.json")  # installed_version 0.5.2, update_available null
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), payload)), \
             patch("leetkit_manager.orchestrator.package_service.latest_pypi_version", return_value="0.6.0"):
            diag = orchestrator.diagnose_lens(STOCKLENS)
        assert diag.report.latest_version == "0.6.0"
        assert diag.report.update_available is True

    def test_pypi_lookup_failure_leaves_update_available_null(self):
        payload = load_fixture("stocklens_doctor.json")
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), payload)), \
             patch("leetkit_manager.orchestrator.package_service.latest_pypi_version", return_value=None):
            diag = orchestrator.diagnose_lens(STOCKLENS)
        assert diag.report.update_available is None

    def test_online_report_with_explicit_update_available_is_not_overridden(self):
        """doctor 자신이 이미 --online으로 확인해서 update_available을 채워왔으면
        (false든 true든) Manager가 재조회로 덮어쓰지 않는다 — 이미 있는 값이 우선."""
        payload = {**load_fixture("stocklens_doctor.json"), "update_available": False, "latest_version": "0.5.2"}
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), payload)), \
             patch("leetkit_manager.orchestrator.package_service.latest_pypi_version") as mock_pypi:
            orchestrator.diagnose_lens(STOCKLENS)
        mock_pypi.assert_not_called()


class TestRunFullDiagnosisAndSummarize:
    def test_summarize_counts_ok_update_and_action_needed(self):
        ok_report = load_fixture("stocklens_doctor.json")
        ok_report["overall"] = "ok"
        ok_report["update_available"] = True
        # 실제 생산자는 overall="ok"이면서 실패한 체크를 함께 내지 않는다 —
        # 픽스처의 CACHE_WRITABLE 실패를 같이 지워야 현실적인 "정상" 상태가 된다.
        for check in ok_report["checks"]:
            if check["status"] == "fail":
                check["status"] = "ok"
        fail_report = load_fixture("dartlens_doctor.json")  # overall == "fail"

        diagnoses = [
            orchestrator.LensDiagnosis(lens=STOCKLENS, report=orchestrator.DoctorReport.from_json(ok_report), process=_fake_process()),
            orchestrator.LensDiagnosis(lens=DARTLENS, report=orchestrator.DoctorReport.from_json(fail_report), process=_fake_process()),
            orchestrator.LensDiagnosis(lens=TELEGRAMLENS, report=None, process=_fake_process(error="not_found"), not_installed=True),
        ]
        summary = orchestrator.summarize(diagnoses)
        assert summary == {"total": 3, "ok": 1, "update_available": 1, "action_needed": 2}

    def test_non_critical_failure_still_counts_as_action_needed(self):
        """StockLens는 critical이 아닌 실패(예: 엑셀 출력 폴더 쓰기 불가)를 overall
        "degraded"로 낮춘다 — 다른 두 Lens는 실패 하나만 있어도 "fail"로 올린다.
        overall만 세면 이 실패가 상단 요약의 "조치 필요"에서 통째로 빠졌다."""
        report = load_fixture("stocklens_doctor.json")  # overall=degraded, CACHE_WRITABLE=fail
        assert report["overall"] == "degraded"
        assert any(c["status"] == "fail" for c in report["checks"])

        diag = orchestrator.LensDiagnosis(
            lens=STOCKLENS, report=orchestrator.DoctorReport.from_json(report), process=_fake_process()
        )
        assert orchestrator.has_actionable_problem(diag) is True
        assert orchestrator.summarize([diag])["action_needed"] == 1

    def test_degraded_without_failing_check_is_not_action_needed(self):
        """경고만 있는 상태(warn)는 조치 필요가 아니다 — 노란불로 충분하다."""
        report = load_fixture("stocklens_doctor.json")
        for check in report["checks"]:
            if check["status"] == "fail":
                check["status"] = "warn"
        diag = orchestrator.LensDiagnosis(
            lens=STOCKLENS, report=orchestrator.DoctorReport.from_json(report), process=_fake_process()
        )
        assert orchestrator.has_actionable_problem(diag) is False

    def test_critical_flag_survives_parsing(self):
        """StockLens만 내는 critical 플래그를 Manager가 통째로 버리고 있었다."""
        report = load_fixture("stocklens_doctor.json")
        report["checks"][0]["critical"] = False
        parsed = orchestrator.DoctorReport.from_json(report)
        assert parsed.checks[0].critical is False
        # 필드를 안 내는 Lens(DartLens/TelegramLens)는 True로 본다 — 그쪽은 fail이면
        # 어차피 전체를 fail로 올리므로 의미가 같다.
        report["checks"][1].pop("critical", None)
        assert orchestrator.DoctorReport.from_json(report).checks[1].critical is True

    def test_run_full_diagnosis_calls_each_lens_once_in_order(self):
        calls = []

        def fake_run_json_cli(cmd, **kwargs):
            calls.append(cmd[0])
            return _fake_process(error="not_found"), None

        with patch.object(orchestrator, "run_json_cli", side_effect=fake_run_json_cli):
            diagnoses = orchestrator.run_full_diagnosis()

        assert calls == [STOCKLENS.doctor_cmd, DARTLENS.doctor_cmd, TELEGRAMLENS.doctor_cmd]
        assert len(diagnoses) == 3

    def test_already_installed_lens_still_gets_silent_auto_repair(self):
        """실사용 중 발견된 문제 재현: DartLens가 이미 예전에 설치돼 있어서(재설치 없이
        라이선스만 새로 활성화) update_lens()의 install 경로를 안 거쳐도, diagnose_lens를
        직접 불러도(대시보드 새로고침·마법사 재진단 등) corp code 캐시 경고가 그대로
        남아있으면 안 된다."""
        stale_report = load_fixture("dartlens_doctor.json")  # CORP_CODE_CACHE: warn
        fresh_report = json.loads(json.dumps(stale_report))
        for check in fresh_report["checks"]:
            if check["id"] == "CORP_CODE_CACHE":
                check["status"] = "ok"
                check["repairable"] = False
                check["repair_id"] = None
        responses = [
            (_fake_process(), stale_report),
            (_fake_process(), {"ok": True}),  # repair 호출
            (_fake_process(), fresh_report),
        ]
        with patch.object(orchestrator, "run_json_cli", side_effect=responses) as mock:
            diag = orchestrator.diagnose_lens(DARTLENS)
        assert mock.call_count == 3
        repair_cmd = mock.call_args_list[1][0][0]
        assert "--repair" in repair_cmd and "corp-code-cache" in repair_cmd
        cache_check = next(c for c in diag.report.checks if c.id == "CORP_CODE_CACHE")
        assert cache_check.status == "ok"

    def test_repeatedly_failing_auto_repair_does_not_infinite_loop(self):
        """복구가 실패해서 warn이 그대로 남아있어도 재확인은 딱 한 번만 — 무한 재귀로
        안 빠진다(doctor 호출은 최초 1회 + 복구 1회 + 재확인 1회 = 정확히 3번)."""
        stale_report = load_fixture("dartlens_doctor.json")  # CORP_CODE_CACHE: warn, 계속 그대로
        responses = [
            (_fake_process(), stale_report),
            (_fake_process(exit_code=1), {"ok": False}),  # repair 실패
            (_fake_process(), stale_report),  # 재확인해도 여전히 warn
        ]
        with patch.object(orchestrator, "run_json_cli", side_effect=responses) as mock:
            diag = orchestrator.diagnose_lens(DARTLENS)
        assert mock.call_count == 3
        cache_check = next(c for c in diag.report.checks if c.id == "CORP_CODE_CACHE")
        assert cache_check.status == "warn"

    def test_lens_without_auto_repair_list_makes_single_call(self):
        ok_payload = load_fixture("stocklens_doctor.json")
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), ok_payload)) as mock:
            orchestrator.diagnose_lens(STOCKLENS)
        assert mock.call_count == 1


class TestSetupLens:
    def test_both_targets_use_both_argument(self):
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), {"ok": True, "targets": []})) as mock:
            orchestrator.setup_lens(STOCKLENS, ["claude-desktop", "claude-code"])
        cmd = mock.call_args[0][0]
        assert cmd == ["stocklens-setup", "--target", "both", "--json", "--non-interactive"]

    def test_single_target(self):
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), {"ok": True, "targets": []})) as mock:
            orchestrator.setup_lens(STOCKLENS, ["claude-code"])
        cmd = mock.call_args[0][0]
        assert "--target" in cmd and "claude-code" in cmd

    def test_empty_targets_raises(self):
        try:
            orchestrator.setup_lens(STOCKLENS, [])
        except ValueError:
            return
        raise AssertionError("expected ValueError for empty targets")

    def test_codex_alone_calls_once_with_codex_target(self):
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), {"ok": True, "targets": ["codex"]})) as mock:
            orchestrator.setup_lens(STOCKLENS, ["codex"])
        mock.assert_called_once()
        cmd = mock.call_args[0][0]
        assert cmd == ["stocklens-setup", "--target", "codex", "--json", "--non-interactive"]

    def test_claude_pair_plus_codex_makes_two_calls_and_merges_targets(self):
        """claude-desktop+claude-code는 "both" 한 번으로 묶이지만, codex는 그걸로
        표현 못 하는 세 번째 타겟이라 별도 호출이 필요하다 — 결과는 병합돼야 한다."""
        responses = [
            (_fake_process(), {"ok": True, "targets": ["claude-desktop", "claude-code"]}),
            (_fake_process(), {"ok": True, "targets": ["codex"]}),
        ]
        with patch.object(orchestrator, "run_json_cli", side_effect=responses) as mock:
            result = orchestrator.setup_lens(STOCKLENS, ["claude-desktop", "claude-code", "codex"])
        assert mock.call_count == 2
        first_cmd = mock.call_args_list[0][0][0]
        second_cmd = mock.call_args_list[1][0][0]
        assert "both" in first_cmd
        assert "codex" in second_cmd
        assert result.ok is True
        assert result.targets == ["claude-desktop", "claude-code", "codex"]

    def test_partial_failure_across_multiple_calls_marks_not_ok(self):
        responses = [
            (_fake_process(), {"ok": True, "targets": ["claude-desktop", "claude-code"]}),
            (_fake_process(exit_code=1), {"ok": False, "error": "codex 설정 실패", "targets": []}),
        ]
        with patch.object(orchestrator, "run_json_cli", side_effect=responses):
            result = orchestrator.setup_lens(STOCKLENS, ["claude-desktop", "claude-code", "codex"])
        assert result.ok is False
        assert result.error == "codex 설정 실패"
        assert result.targets == ["claude-desktop", "claude-code"]

    def test_argparse_rejected_target_shows_update_needed_message_not_raw_parse_error(self):
        """실사용 중 발견된 문제 재현: 설치된 Lens가 아직 --target codex를 모르는 옛
        버전이면 argparse가 exit=2로 usage/invalid choice를 stderr에 찍고 JSON은
        하나도 안 나온다 — payload=None인 건 다른 진짜 파싱 실패와 같지만, 사용자에게는
        "파싱할 수 없습니다"가 아니라 "업데이트가 필요합니다"로 보여야 한다."""
        stale_cli_response = ProcessResult(
            cmd=["stocklens-setup"], exit_code=2, timed_out=False, duration_s=0.01,
            stdout="usage: stocklens-setup [-h] [--target {claude-desktop,claude-code,both,auto}]\n",
            stderr="stocklens-setup: error: argument --target: invalid choice: 'codex' (choose from 'claude-desktop', 'claude-code', 'both', 'auto')\n",
        )
        with patch.object(orchestrator, "run_json_cli", return_value=(stale_cli_response, None)):
            result = orchestrator.setup_lens(STOCKLENS, ["codex"])
        assert result.ok is False
        assert "업데이트" in result.error
        assert "파싱할 수 없습니다" not in result.error


class TestRegisterApiKey:
    def test_unsupported_credential_kind_raises_without_calling_process(self):
        with patch.object(orchestrator, "run_json_cli") as mock:
            try:
                orchestrator.register_api_key(STOCKLENS, "dart_api", "secret")
            except ValueError:
                pass
            else:
                raise AssertionError("expected ValueError")
        mock.assert_not_called()

    def test_key_passed_via_stdin_not_cmd(self):
        secret = "FAKE-DART-API-KEY"
        payload = {"api_key_saved": True, "storage": "os-keychain", "targets": ["claude-code"], "key_tail_masked": "****abcd"}
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), payload)) as mock:
            result = orchestrator.register_api_key(DARTLENS, "dart_api", secret)
        cmd, kwargs = mock.call_args[0], mock.call_args[1]
        assert secret not in cmd[0]
        assert kwargs["input_text"] == secret
        assert cmd[0] == ["dartlens-setup", "--api-key-stdin", "--json", "--non-interactive"]
        assert result.ok is True

    def test_targets_appended_when_given(self):
        payload = {"api_key_saved": True, "targets": ["claude-desktop"]}
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), payload)) as mock:
            orchestrator.register_api_key(DARTLENS, "dart_api", "key", targets=["claude-desktop", "claude-code"])
        cmd = mock.call_args[0][0]
        assert cmd == ["dartlens-setup", "--api-key-stdin", "--json", "--non-interactive", "--target", "both"]

    def test_not_found_maps_to_not_installed(self):
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(error="not_found"), None)):
            result = orchestrator.register_api_key(DARTLENS, "dart_api", "key")
        assert result.ok is False
        assert result.error_code == "not_installed"

    def test_argparse_rejected_flag_shows_update_needed_message(self):
        stale_cli_response = ProcessResult(
            cmd=["dartlens-setup"], exit_code=2, timed_out=False, duration_s=0.01,
            stdout="",
            stderr="dartlens-setup: error: unrecognized arguments: --api-key-stdin\n",
        )
        with patch.object(orchestrator, "run_json_cli", return_value=(stale_cli_response, None)):
            result = orchestrator.register_api_key(DARTLENS, "dart_api", "key")
        assert result.ok is False
        assert "업데이트" in result.error


class TestActivateLens:
    def test_license_key_passed_via_stdin_not_cmd(self):
        secret = "FAKE-LICENSE-KEY"
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), {"ok": True, "license_id_masked": "****ABCD"})) as mock:
            result = orchestrator.activate_lens(STOCKLENS, secret)
        _cmd, kwargs = mock.call_args[0], mock.call_args[1]
        assert secret not in _cmd[0]
        assert kwargs["input_text"] == secret
        assert result.ok is True


class TestRepairLens:
    def test_unsupported_repair_id_raises_without_calling_process(self):
        with patch.object(orchestrator, "run_json_cli") as mock:
            try:
                orchestrator.repair_lens(STOCKLENS, "corp-code-cache")
            except ValueError:
                pass
            else:
                raise AssertionError("expected ValueError")
        mock.assert_not_called()

    def test_supported_repair_id_calls_doctor_with_repair_flags(self):
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), {"repaired": True})) as mock:
            orchestrator.repair_lens(DARTLENS, "corp-code-cache")
        cmd = mock.call_args[0][0]
        assert cmd == ["dartlens-doctor", "--json", "--repair", "corp-code-cache", "--yes"]


class TestUpdateLens:
    def test_successful_update_has_no_rollback_command(self):
        ok_payload = load_fixture("stocklens_doctor.json")
        with patch("leetkit_manager.orchestrator.package_service.install_version", return_value=_fake_process(exit_code=0)), \
             patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), ok_payload)):
            result = orchestrator.update_lens(STOCKLENS, "0.5.3", previous_version="0.5.2")
        assert result.ok is True
        assert result.rollback_command is None

    def test_failed_install_surfaces_rollback_command(self):
        with patch("leetkit_manager.orchestrator.package_service.install_version", return_value=_fake_process(exit_code=1)):
            result = orchestrator.update_lens(STOCKLENS, "0.5.3", previous_version="0.5.2")
        assert result.ok is False
        assert result.rollback_command == "uv tool install --force stocklens-mcp==0.5.2"

    def test_dartlens_install_auto_repairs_corp_code_cache_without_user_action(self):
        """실사용 중 나온 요청: DartLens는 첫 설치 후 corp code 캐시가 없어서 대시보드에
        경고가 뜨는데, 사용자가 굳이 복구 버튼을 누르지 않아도 설치 직후 바로 정상으로
        보이게 해야 한다 — lens_contract의 auto_repair_after_install이 이걸 명시한다."""
        stale_report = load_fixture("dartlens_doctor.json")  # CORP_CODE_CACHE: warn, repairable
        fresh_report = json.loads(json.dumps(stale_report))
        for check in fresh_report["checks"]:
            if check["id"] == "CORP_CODE_CACHE":
                check["status"] = "ok"
                check["repairable"] = False
                check["repair_id"] = None
        responses = [
            (_fake_process(), stale_report),   # 설치 직후 최초 diagnose
            (_fake_process(), {"ok": True}),   # repair 호출
            (_fake_process(), fresh_report),   # 복구 후 재-diagnose
        ]
        with patch("leetkit_manager.orchestrator.package_service.install_version", return_value=_fake_process(exit_code=0)), \
             patch.object(orchestrator, "run_json_cli", side_effect=responses) as mock:
            result = orchestrator.update_lens(DARTLENS, "0.6.7")
        assert result.ok is True
        assert result.post_doctor.report.checks[
            next(i for i, c in enumerate(result.post_doctor.report.checks) if c.id == "CORP_CODE_CACHE")
        ].status == "ok"
        repair_cmd = mock.call_args_list[1][0][0]
        assert "--repair" in repair_cmd and "corp-code-cache" in repair_cmd

    def test_lens_without_auto_repair_list_skips_extra_calls(self):
        """StockLens는 auto_repair_after_install이 비어 있으므로, warn 체크가 있어도
        추가 repair/재-diagnose 호출 없이 설치 직후 진단 결과를 그대로 써야 한다."""
        ok_payload = load_fixture("stocklens_doctor.json")
        with patch("leetkit_manager.orchestrator.package_service.install_version", return_value=_fake_process(exit_code=0)), \
             patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), ok_payload)) as mock:
            orchestrator.update_lens(STOCKLENS, "0.5.3")
        assert mock.call_count == 1  # diagnose 한 번뿐 — repair나 재-diagnose 없음


class TestUninstallLens:
    def test_successful_uninstall_marks_not_installed(self):
        with patch("leetkit_manager.orchestrator.package_service.uninstall_version", return_value=_fake_process(exit_code=0)), \
             patch.object(orchestrator.package_service, "find_legacy_pip_shadow", return_value=None), \
             patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(error="not_found"), None)):
            result = orchestrator.uninstall_lens(STOCKLENS)
        assert result.ok is True
        assert result.post_doctor.not_installed is True

    def test_uv_uninstall_failure_is_not_ok(self):
        with patch("leetkit_manager.orchestrator.package_service.uninstall_version", return_value=_fake_process(exit_code=1)) as mock_uninstall, \
             patch.object(orchestrator.package_service, "find_legacy_pip_shadow", return_value=None), \
             patch.object(orchestrator, "run_json_cli") as mock_diag:
            result = orchestrator.uninstall_lens(STOCKLENS)
        assert result.ok is False
        assert result.post_doctor is None  # 삭제 자체가 실패했으면 재진단도 안 함
        mock_diag.assert_not_called()
        mock_uninstall.assert_called_once()

    def test_still_resolvable_after_uninstall_is_not_ok(self):
        """uv 관리 밖의 낡은 실행 파일이 PATH에 남아있으면 uv tool uninstall은
        성공해도 doctor는 여전히 그 낡은 걸 찾아버릴 수 있다 — 그 경우까지 ok로
        보고하면 안 된다(사용자가 "삭제됐다는데 왜 카드가 그대로냐"고 헷갈릴 것)."""
        stale_payload = load_fixture("stocklens_doctor.json")
        with patch("leetkit_manager.orchestrator.package_service.uninstall_version", return_value=_fake_process(exit_code=0)), \
             patch.object(orchestrator.package_service, "find_legacy_pip_shadow", return_value=None), \
             patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), stale_payload)):
            result = orchestrator.uninstall_lens(STOCKLENS)
        assert result.ok is False
        assert result.post_doctor.not_installed is False

    def test_legacy_pip_shadow_gets_cleaned_up_too(self, tmp_path):
        """실사용 중 나온 요청: 옛 pip 설치 잔재도 이번 삭제에 같이 정리돼야 재설치가
        진짜로 깨끗하게 된다 — uv tool uninstall만으로는 안 풀리던 문제."""
        shadow_path = tmp_path / "stocklens-doctor.exe"
        shadow_path.write_text("", encoding="utf-8")
        with patch("leetkit_manager.orchestrator.package_service.uninstall_version", return_value=_fake_process(exit_code=0)), \
             patch.object(orchestrator.package_service, "find_legacy_pip_shadow", return_value=shadow_path), \
             patch.object(
                 orchestrator.package_service, "uninstall_legacy_pip_shadow",
                 return_value=_fake_process(exit_code=0),
             ) as mock_legacy_uninstall, \
             patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(error="not_found"), None)):
            result = orchestrator.uninstall_lens(STOCKLENS)
        mock_legacy_uninstall.assert_called_once_with(STOCKLENS.package_name, shadow_path)
        assert result.ok is True
        assert result.post_doctor.not_installed is True

    def test_legacy_pip_shadow_removal_failure_is_not_ok(self, tmp_path):
        """uv 쪽은 지워졌어도 pip 잔재를 못 지웠으면(python.exe 못 찾음 등) 전체를
        성공으로 보고하면 안 된다 — 여전히 "호환되지 않는 버전"이 남을 것이기 때문."""
        shadow_path = tmp_path / "stocklens-doctor.exe"
        shadow_path.write_text("", encoding="utf-8")
        legacy_failure = ProcessResult(
            cmd=["pip"], exit_code=1, stdout="", stderr="python.exe를 찾을 수 없습니다",
            timed_out=False, duration_s=0.0,
        )
        with patch("leetkit_manager.orchestrator.package_service.uninstall_version", return_value=_fake_process(exit_code=0)), \
             patch.object(orchestrator.package_service, "find_legacy_pip_shadow", return_value=shadow_path), \
             patch.object(orchestrator.package_service, "uninstall_legacy_pip_shadow", return_value=legacy_failure), \
             patch.object(orchestrator, "run_json_cli") as mock_diag:
            result = orchestrator.uninstall_lens(STOCKLENS)
        assert result.ok is False
        assert result.uninstall is legacy_failure  # 실패 원인이 그대로 보여야 함
        mock_diag.assert_not_called()  # 완전히 지워지지 않았으면 재진단도 의미 없음


class TestTelegramLogin:
    def setup_method(self):
        # 모듈 전역 세션 상태 — 테스트 간 오염 방지.
        orchestrator._login_session = None

    def teardown_method(self):
        orchestrator._login_session = None

    def test_start_returns_first_status_from_process(self):
        fake_proc = type("Fake", (), {"read_first": lambda self, timeout=30.0: {"status": "need_phone"}, "close": lambda self: None})()
        with patch("leetkit_manager.interactive_process.InteractiveProcess", return_value=fake_proc):
            result = orchestrator.start_telegram_login()
        assert result == {"status": "need_phone"}

    def test_start_uses_stepper_flag_and_resolved_command(self):
        captured_cmd = {}

        class _Recording:
            def __init__(self, cmd):
                captured_cmd["cmd"] = cmd

            def read_first(self, timeout=30.0):
                return {"status": "need_phone"}

            def close(self):
                pass

        with patch("leetkit_manager.interactive_process.InteractiveProcess", side_effect=_Recording), \
             patch.object(orchestrator.package_service, "resolve_lens_command", return_value="telegramlens-login"):
            orchestrator.start_telegram_login()
        assert captured_cmd["cmd"] == ["telegramlens-login", "--stepper"]

    def test_start_failure_when_process_yields_no_first_status(self):
        fake_proc = type("Fake", (), {"read_first": lambda self, timeout=30.0: None, "close": lambda self: None})()
        with patch("leetkit_manager.interactive_process.InteractiveProcess", return_value=fake_proc):
            result = orchestrator.start_telegram_login()
        assert result["status"] == "error"
        assert result["code"] == "START_FAILED"

    def test_step_without_session_returns_no_session_error(self):
        result = orchestrator.send_telegram_login_step({"phone": "+821012345678"})
        assert result == {"status": "error", "code": "NO_SESSION", "message": "로그인이 시작되지 않았습니다."}

    def test_step_forwards_to_session_and_returns_reply(self):
        fake_proc = type(
            "Fake", (),
            {
                "read_first": lambda self, timeout=30.0: {"status": "need_phone"},
                "send": lambda self, payload, timeout=30.0: {"status": "code_sent"},
                "close": lambda self: None,
            },
        )()
        with patch("leetkit_manager.interactive_process.InteractiveProcess", return_value=fake_proc):
            orchestrator.start_telegram_login()
        result = orchestrator.send_telegram_login_step({"phone": "+821012345678"})
        assert result == {"status": "code_sent"}

    def test_step_no_response_maps_to_error(self):
        fake_proc = type(
            "Fake", (),
            {
                "read_first": lambda self, timeout=30.0: {"status": "need_phone"},
                "send": lambda self, payload, timeout=30.0: None,
                "close": lambda self: None,
            },
        )()
        with patch("leetkit_manager.interactive_process.InteractiveProcess", return_value=fake_proc):
            orchestrator.start_telegram_login()
        result = orchestrator.send_telegram_login_step({"code": "999999"})
        assert result["status"] == "error"
        assert result["code"] == "NO_RESPONSE"

    def test_cancel_closes_session_and_clears_state(self):
        closed = {"called": False}

        def _close(self):
            closed["called"] = True

        fake_proc = type("Fake", (), {"read_first": lambda self, timeout=30.0: {"status": "need_phone"}, "close": _close})()
        with patch("leetkit_manager.interactive_process.InteractiveProcess", return_value=fake_proc):
            orchestrator.start_telegram_login()
        orchestrator.cancel_telegram_login()
        assert closed["called"] is True
        assert orchestrator._login_session is None

    def test_starting_again_cancels_previous_session(self):
        first_closed = {"called": False}

        def _close(self):
            first_closed["called"] = True

        first_proc = type("Fake", (), {"read_first": lambda self, timeout=30.0: {"status": "need_phone"}, "close": _close})()
        second_proc = type("Fake", (), {"read_first": lambda self, timeout=30.0: {"status": "need_credentials"}, "close": lambda self: None})()
        with patch("leetkit_manager.interactive_process.InteractiveProcess", side_effect=[first_proc, second_proc]):
            orchestrator.start_telegram_login()
            orchestrator.start_telegram_login()
        assert first_closed["called"] is True

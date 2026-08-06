"""orchestrator.py 단위 테스트.

실제 uv/글로벌 설치된 Lens CLI에 의존하지 않는다 — `run_json_cli`/`run_cli`를
monkeypatch해서 "이 JSON이 오면 orchestrator가 이렇게 해석한다"만 검증한다.
subprocess 자체(timeout/stdin 전달 등)는 test_process_runner.py가 이미 커버한다.
"""

from __future__ import annotations

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

    def test_unsupported_schema_version_marks_incompatible(self):
        payload = {**load_fixture("stocklens_doctor.json"), "schema_version": 999}
        with patch.object(orchestrator, "run_json_cli", return_value=(_fake_process(), payload)):
            diag = orchestrator.diagnose_lens(STOCKLENS)
        assert diag.incompatible is True


class TestRunFullDiagnosisAndSummarize:
    def test_summarize_counts_ok_update_and_action_needed(self):
        ok_report = load_fixture("stocklens_doctor.json")
        ok_report["overall"] = "ok"
        ok_report["update_available"] = True
        fail_report = load_fixture("dartlens_doctor.json")  # overall == "fail"

        diagnoses = [
            orchestrator.LensDiagnosis(lens=STOCKLENS, report=orchestrator.DoctorReport.from_json(ok_report), process=_fake_process()),
            orchestrator.LensDiagnosis(lens=DARTLENS, report=orchestrator.DoctorReport.from_json(fail_report), process=_fake_process()),
            orchestrator.LensDiagnosis(lens=TELEGRAMLENS, report=None, process=_fake_process(error="not_found"), not_installed=True),
        ]
        summary = orchestrator.summarize(diagnoses)
        assert summary == {"total": 3, "ok": 1, "update_available": 1, "action_needed": 2}

    def test_run_full_diagnosis_calls_each_lens_once_in_order(self):
        calls = []

        def fake_run_json_cli(cmd, **kwargs):
            calls.append(cmd[0])
            return _fake_process(error="not_found"), None

        with patch.object(orchestrator, "run_json_cli", side_effect=fake_run_json_cli):
            diagnoses = orchestrator.run_full_diagnosis()

        assert calls == [STOCKLENS.doctor_cmd, DARTLENS.doctor_cmd, TELEGRAMLENS.doctor_cmd]
        assert len(diagnoses) == 3


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

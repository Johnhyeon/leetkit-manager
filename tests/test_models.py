from __future__ import annotations

from conftest import load_fixture

from leetkit_manager.models import ActivateResult, DoctorReport, SetupResult


def test_stocklens_doctor_report_parses_all_top_level_fields():
    report = DoctorReport.from_json(load_fixture("stocklens_doctor.json"))
    assert report.schema_version == 1
    assert report.product == "stocklens"
    assert report.package_name == "stocklens-mcp"
    assert report.installed_version == "0.5.2"
    assert report.overall == "degraded"
    assert report.license.status == "active"
    assert report.license.license_id_masked == "****3972"
    assert report.targets == ["claude-desktop"]
    assert len(report.checks) == 4
    assert report.readiness == "주의"


def test_dartlens_doctor_report_keeps_extension_fields_in_raw():
    payload = load_fixture("dartlens_doctor.json")
    report = DoctorReport.from_json(payload)
    assert report.overall == "fail"
    assert report.readiness == "조치 필요"
    # dart_api/corp_code_cache는 공통 모델 필드가 아니라 raw로만 접근 가능해야 한다.
    assert report.raw["dart_api"]["status"] == "valid"
    assert report.raw["corp_code_cache"]["error_code"] == "CORP_CODE_CACHE_STALE"
    cache_check = next(c for c in report.checks if c.id == "CORP_CODE_CACHE")
    assert cache_check.repairable is True
    assert cache_check.repair_id == "corp-code-cache"


def test_telegramlens_doctor_report_daemon_check_is_repairable():
    report = DoctorReport.from_json(load_fixture("telegramlens_doctor.json"))
    daemon_check = next(c for c in report.checks if c.id == "DAEMON_COLLECTOR")
    assert daemon_check.status == "fail"
    assert daemon_check.repairable is True
    assert daemon_check.repair_id == "daemon"


def test_activate_result_normalizes_stocklens_ok_field():
    result = ActivateResult.from_json({"ok": True, "status": "active", "license_id_masked": "****ABCD"})
    assert result.ok is True
    assert result.license_id_masked == "****ABCD"


def test_activate_result_normalizes_dartlens_license_activated_field():
    """DartLens는 의도적으로 `ok`가 아니라 `license_activated`를 쓴다 — Manager가 흡수한다."""
    result = ActivateResult.from_json(
        {"license_activated": False, "error_code": "DARTLENS_LICENSE_INVALID", "message": "서명 불일치"}
    )
    assert result.ok is False
    assert result.error_code == "DARTLENS_LICENSE_INVALID"
    assert result.message == "서명 불일치"


def test_activate_result_falls_back_to_exit_code_when_json_unparseable():
    result = ActivateResult.from_json({}, exit_code=1)
    assert result.ok is False


def test_setup_result_normalizes_dartlens_api_key_saved_field():
    result = SetupResult.from_json(
        {"api_key_saved": True, "storage": "os-keychain", "targets": ["claude-code"], "key_tail_masked": "****cebf"}
    )
    assert result.ok is True
    assert result.targets == ["claude-code"]


def test_setup_result_handles_stocklens_dict_targets():
    result = SetupResult.from_json(
        {"ok": True, "targets": [{"target_label": "Claude Desktop", "config_path": "/tmp/x.json", "backup_path": None}]}
    )
    assert result.ok is True
    assert result.targets[0]["target_label"] == "Claude Desktop"

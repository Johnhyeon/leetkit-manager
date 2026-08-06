from __future__ import annotations

from unittest.mock import patch

from leetkit_manager.package_service import resolve_lens_command


def test_resolve_lens_command_falls_back_to_bare_name_when_not_in_uv_bin_dirs(tmp_path):
    with patch("leetkit_manager.package_service._uv_tool_bin_dirs", return_value=[tmp_path]):
        assert resolve_lens_command("stocklens-doctor") == "stocklens-doctor"


def test_resolve_lens_command_prefers_uv_tool_bin_dir_over_bare_name(tmp_path):
    """실사용 중 발견된 문제 재현: uv tool bin dir에 있는 최신 실행 파일을 PATH
    검색보다 먼저 찾아야 한다(옛 pip 설치 잔재가 PATH에서 먼저 잡히는 경우 대비)."""
    fake_exe = tmp_path / "stocklens-doctor.exe"
    fake_exe.write_text("", encoding="utf-8")

    with patch("leetkit_manager.package_service._uv_tool_bin_dirs", return_value=[tmp_path]):
        resolved = resolve_lens_command("stocklens-doctor")

    assert resolved == str(fake_exe)

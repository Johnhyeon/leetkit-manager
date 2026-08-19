"""설치·업데이트 때 옛 pip 설치본을 정리하는지.

예전 배포는 pip 였다. 그 시절 고객 PC 에는 시스템 Python 의 Scripts 에 실행 파일이
남아 있고, PATH 순서상 그게 uv 관리본보다 먼저 잡힌다 — Manager 는 최신을 깔아놓고
"최신"이라 말하는데 터미널·설정 파일은 옛 것을 가리킨다. 실기기에서 실제로 그 상태였다
(세 호스트 모두 StockLens 0.5.0 을 띄우고 있었다).

여기서 지키는 계약은 두 가지다.
1) uv 설치가 **성공한 뒤에만** 옛 것을 지운다 — 순서가 반대면 설치 실패 시 아무것도 안 남는다.
2) 못 지워도 업데이트를 실패로 만들지 않되, 그 사실을 결과에 남긴다.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from leetkit_manager import orchestrator, package_service
from leetkit_manager.lens_contract import STOCKLENS
from leetkit_manager.process_runner import ProcessResult


def _proc(ok=True, stderr=""):
    return ProcessResult(cmd=["x"], exit_code=0 if ok else 1, timed_out=False,
                         duration_s=0.0, stdout="", stderr=stderr)


SHADOW = Path("C:/Python311/Scripts/stocklens.exe")


def _run(*, install_ok, shadow, uninstall_ok=True, stderr=""):
    with patch.object(package_service, "install_version", return_value=_proc(install_ok)), \
         patch.object(package_service, "find_legacy_pip_shadow", return_value=shadow) as mock_find, \
         patch.object(package_service, "uninstall_legacy_pip_shadow",
                      return_value=_proc(uninstall_ok, stderr)) as mock_rm, \
         patch.object(orchestrator, "diagnose_lens",
                      return_value=MagicMock(incompatible=False, not_installed=False)):
        result = orchestrator.update_lens(STOCKLENS, "0.8.0")
    return result, mock_find, mock_rm


def test_removes_the_old_pip_copy_after_a_successful_install():
    result, _find, mock_rm = _run(install_ok=True, shadow=SHADOW)
    mock_rm.assert_called_once_with(STOCKLENS.package_name, SHADOW)
    assert result.legacy_pip_removed == str(SHADOW)
    assert result.legacy_pip_error is None
    assert result.ok is True


def test_does_not_touch_anything_when_the_install_failed():
    """먼저 지우고 설치가 실패하면 고객에게 아무것도 안 남는다 — 순서를 못으로 박아둔다."""
    result, mock_find, mock_rm = _run(install_ok=False, shadow=SHADOW)
    mock_find.assert_not_called()
    mock_rm.assert_not_called()
    assert result.legacy_pip_removed is None


def test_nothing_to_clean_is_the_normal_case():
    result, _find, mock_rm = _run(install_ok=True, shadow=None)
    mock_rm.assert_not_called()
    assert result.legacy_pip_removed is None
    assert result.legacy_pip_error is None


def test_a_failed_cleanup_does_not_fail_the_update_but_is_reported():
    result, _find, _rm = _run(install_ok=True, shadow=SHADOW, uninstall_ok=False,
                              stderr="python.exe를 찾을 수 없습니다")
    assert result.ok is True           # 새 버전은 이미 깔렸다
    assert result.legacy_pip_removed is None
    assert "python.exe" in result.legacy_pip_error


# ── 개발용(editable) 설치 보호 ──────────────────────────────────────────────
# `pip install -e` 로 깔린 것도 PATH 에서 uv 밖으로 잡혀 "옛 잔재"와 생김새가 같다.
# 고객 PC 에는 없지만 개발 PC 에는 있고, 지우면 작업 환경이 통째로 사라진다.


def _fake_python(tmp_path):
    """<python.exe> + Lib/site-packages 레이아웃을 흉내낸다(윈도우 표준 설치)."""
    python_exe = tmp_path / "python.exe"
    python_exe.write_text("", encoding="utf-8")
    site = tmp_path / "Lib" / "site-packages"
    site.mkdir(parents=True)
    return python_exe, site


def test_editable_detected_via_direct_url(tmp_path):
    """표준(PEP 610) 표시. pip 출력을 읽는 방식은 실기기에서 pip 자신의 로깅 오류로
    출력이 잘려 오판했다 — 그래서 파일을 본다."""
    python_exe, site = _fake_python(tmp_path)
    info = site / "telegramlens_mcp-0.4.2.dist-info"
    info.mkdir()
    (info / "direct_url.json").write_text(
        '{"dir_info": {"editable": true}, "url": "file:///D:/repo"}', encoding="utf-8"
    )
    assert package_service.is_editable_install(python_exe, "telegramlens-mcp") is True


def test_editable_detected_via_pth_marker(tmp_path):
    """옛 방식·일부 백엔드는 .pth 흔적만 남긴다."""
    python_exe, site = _fake_python(tmp_path)
    (site / "_editable_impl_telegramlens_mcp.pth").write_text("", encoding="utf-8")
    assert package_service.is_editable_install(python_exe, "telegramlens-mcp") is True


def test_normal_install_is_not_editable(tmp_path):
    python_exe, site = _fake_python(tmp_path)
    info = site / "stocklens_mcp-0.5.0.dist-info"
    info.mkdir()
    (info / "direct_url.json").write_text('{"url": "https://pypi.org/x"}', encoding="utf-8")
    assert package_service.is_editable_install(python_exe, "stocklens-mcp") is False


def test_cleanup_refuses_to_delete_an_editable_install(tmp_path):
    """지우지 않고, 지우지 않았다는 사실을 이유와 함께 돌려준다."""
    python_exe, site = _fake_python(tmp_path)
    (site / "_editable_impl_stocklens_mcp.pth").write_text("", encoding="utf-8")
    shadow = tmp_path / "Scripts" / "stocklens.exe"
    shadow.parent.mkdir()
    shadow.write_text("", encoding="utf-8")
    with patch.object(package_service, "_infer_python_for_script", return_value=python_exe), \
         patch.object(package_service, "run_cli") as mock_run:
        result = package_service.uninstall_legacy_pip_shadow("stocklens-mcp", shadow)
    mock_run.assert_not_called()   # pip uninstall 을 아예 부르지 않는다
    assert result.ok is False
    assert "개발용" in result.stderr

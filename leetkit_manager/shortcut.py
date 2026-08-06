"""바탕화면 바로가기 생성 — 최초 실행 후에는 터미널 명령을 다시 기억할 필요가 없게 한다.

`uv tool install`이 만드는 `leetkit-manager(.exe)`는 이미 진짜 실행 파일이다(래퍼 exe) —
새로 실행 파일을 빌드할 필요 없이 그 exe를 가리키는 바로가기만 바탕화면에 놓으면 된다.
"""

from __future__ import annotations

import sys
from pathlib import Path

from leetkit_manager import package_service

_MARKER = Path.home() / ".leetkit-manager" / "shortcut_created"


def _resolved_exe_path() -> str:
    """실행 중인 leetkit-manager(.exe)의 실제 경로. 못 찾으면 bare 이름(코드 서명 없는
    개발 환경 등)을 그대로 반환 — 그 경우 바로가기는 만들어도 동작 안 할 수 있다."""
    return package_service.resolve_lens_command("leetkit-manager")


def create_desktop_shortcut() -> Path | None:
    """바탕화면에 LeetKit Manager 바로가기를 만든다. 실패해도 앱 실행 자체는 막지 않는다."""
    try:
        if sys.platform == "win32":
            return _create_windows_shortcut()
        if sys.platform == "darwin":
            return _create_macos_alias()
        return None
    except Exception:
        return None


def _create_windows_shortcut() -> Path | None:
    import win32com.client  # noqa: PLC0415 — Windows 전용이라 여기서만 import

    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        return None
    link_path = desktop / "LeetKit Manager.lnk"

    target = _resolved_exe_path()
    if not Path(target).exists():
        return None  # bare 이름만 남았으면(PATH 의존) 바로가기가 안전하지 않으니 만들지 않는다.

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(link_path))
    shortcut.TargetPath = target
    shortcut.Arguments = "gui"
    shortcut.WorkingDirectory = str(Path(target).parent)
    shortcut.Description = "LeetKit Manager — StockLens/DartLens/TelegramLens 진단·설치"
    icon_path = Path(__file__).parent / "ui" / "icon.ico"
    if icon_path.exists():
        shortcut.IconLocation = str(icon_path)
    shortcut.save()
    return link_path


def _create_macos_alias() -> Path | None:
    """macOS는 .lnk 개념이 없다 — 심볼릭 링크로 최소한의 더블클릭 진입점을 제공한다.
    (실제 .app 번들만큼 매끄럽진 않다 — 검증 안 됨, 추후 개선 여지.)"""
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        return None
    target = _resolved_exe_path()
    if not Path(target).exists():
        return None
    link_path = desktop / "LeetKit Manager"
    if link_path.exists():
        return link_path
    link_path.symlink_to(target)
    return link_path


def ensure_desktop_shortcut_once() -> None:
    """최초 GUI 실행 때만 바로가기를 만든다(그다음부터는 조용히 넘어감)."""
    if _MARKER.exists():
        return
    _MARKER.parent.mkdir(parents=True, exist_ok=True)
    create_desktop_shortcut()
    _MARKER.write_text("1", encoding="utf-8")

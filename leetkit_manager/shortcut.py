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
    """실행 중인 leetkit-manager(.exe)의 실제 경로.

    PyInstaller 단일 exe(`sys.frozen`)로 실행 중이면 그 자신이 곧 실행 파일이므로
    `sys.executable`이 정답이다 — `uv tool install`이 만드는 `~/.local/bin/leetkit-manager`
    래퍼를 찾는 resolve_lens_command로는 못 찾는다(실사용 중 발견: exe를 아무 폴더에
    복사해 실행하면 uv tool bin 디렉터리에 그 이름이 없어 bare 문자열 "leetkit-manager"가
    그대로 반환되고, 그건 실제 파일이 아니라서 create_shortcut_at()이 아무 에러도 없이
    조용히 바로가기를 안 만들었다). uv tool install로 깐 경우에만 기존처럼
    resolve_lens_command로 실제 wrapper exe 경로를 찾는다."""
    if package_service.is_frozen_exe():
        return sys.executable
    return package_service.resolve_lens_command("leetkit-manager")


def create_shortcut_at(target_dir: Path) -> Path | None:
    """target_dir에 LeetKit Manager 바로가기를 만든다. 실패해도 앱 실행 자체는 막지 않는다."""
    try:
        if sys.platform == "win32":
            return _create_windows_shortcut(target_dir)
        if sys.platform == "darwin":
            return _create_macos_alias(target_dir)
        return None
    except Exception:
        return None


def create_desktop_shortcut() -> Path | None:
    """기본 위치(바탕화면)에 바로가기 — 사용자가 저장 위치를 직접 고르지 않을 때(다이얼로그
    취소 등)의 대체 동작."""
    return create_shortcut_at(Path.home() / "Desktop")


def _create_windows_shortcut(target_dir: Path) -> Path | None:
    import win32com.client  # noqa: PLC0415 — Windows 전용이라 여기서만 import

    if not target_dir.exists():
        return None
    link_path = target_dir / "LeetKit Manager.lnk"

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


def _create_macos_alias(target_dir: Path) -> Path | None:
    """macOS는 .lnk 개념이 없다 — 심볼릭 링크로 최소한의 더블클릭 진입점을 제공한다.
    (실제 .app 번들만큼 매끄럽진 않다 — 검증 안 됨, 추후 개선 여지.)"""
    if not target_dir.exists():
        return None
    target = _resolved_exe_path()
    if not Path(target).exists():
        return None
    link_path = target_dir / "LeetKit Manager"
    if link_path.exists():
        return link_path
    link_path.symlink_to(target)
    return link_path


def has_shortcut_been_offered() -> bool:
    """이미 한 번(위치 선택이든 기본값이든) 바로가기 생성을 시도했는지 — 온보딩 마법사가
    이 단계를 다시 보여줄지 판단하는 데 쓴다."""
    return _MARKER.exists()


def mark_shortcut_offered() -> None:
    _MARKER.parent.mkdir(parents=True, exist_ok=True)
    _MARKER.write_text("1", encoding="utf-8")

"""바탕화면 바로가기 생성 — 최초 실행 후에는 터미널 명령을 다시 기억할 필요가 없게 한다.

`uv tool install`이 만드는 `leetkit-manager(.exe)`는 이미 진짜 실행 파일이다(래퍼 exe) —
새로 실행 파일을 빌드할 필요 없이 그 exe를 가리키는 바로가기만 바탕화면에 놓으면 된다.
"""

from __future__ import annotations

import os
import shutil
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
            return _create_macos_app_bundle(target_dir)
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


_MACOS_INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>LeetKit Manager</string>
  <key>CFBundleDisplayName</key><string>LeetKit Manager</string>
  <key>CFBundleExecutable</key><string>LeetKitManager</string>
  <key>CFBundleIdentifier</key><string>com.leetkit.manager</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>{version}</string>
  <key>CFBundleVersion</key><string>{version}</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


def _create_macos_app_bundle(target_dir: Path) -> Path | None:
    """macOS는 .lnk 개념이 없다 — 최소 구성의 .app 번들을 만든다.

    예전엔 심볼릭 링크였는데 실사용에서 세 가지가 한꺼번에 문제였다:
    아이콘이 아예 안 붙고(링크에는 지정할 방법이 없다), 도크·메뉴막대 이름이
    "Python"으로 뜨고(실행 주체가 파이썬 인터프리터라서), 더블클릭하면 터미널 창이
    같이 떴다. .app 번들은 이 셋을 한 번에 해결한다 — 이름은 Info.plist의
    CFBundleName, 아이콘은 Resources/icon.icns, 터미널은 애초에 안 뜬다.

    번들은 폴더일 뿐이라 별도 도구가 필요 없다. 아이콘도 빌드 때 미리 만들어 둔
    icon.icns를 복사만 한다(맥에서 sips/iconutil을 부르지 않으므로 실패할 구석이 없다).
    """
    from leetkit_manager import __version__

    if not target_dir.exists():
        return None
    target = _resolved_exe_path()
    if not Path(target).exists():
        return None

    app_path = target_dir / "LeetKit Manager.app"
    macos_dir = app_path / "Contents" / "MacOS"
    resources_dir = app_path / "Contents" / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    (app_path / "Contents" / "Info.plist").write_text(
        _MACOS_INFO_PLIST.format(version=__version__), encoding="utf-8"
    )

    # 경로에 공백이 있어도(예: /Users/홍 길동/...) 깨지지 않게 따옴표로 감싼다.
    launcher = macos_dir / "LeetKitManager"
    launcher.write_text(f'#!/bin/sh\nexec "{target}" gui\n', encoding="utf-8")
    launcher.chmod(0o755)  # 실행 권한이 없으면 Finder가 번들을 아예 안 연다

    icns = Path(__file__).parent / "ui" / "icon.icns"
    if icns.exists():
        shutil.copy2(icns, resources_dir / "icon.icns")

    # Finder는 번들 아이콘을 캐시한다 — 내용만 바꾸면 옛 아이콘이 그대로 보인다.
    # 번들 자체의 수정 시각을 건드려 다시 읽게 한다.
    try:
        os.utime(app_path, None)
    except OSError:
        pass

    # 예전 버전이 만든 심볼릭 링크가 남아 있으면 아이콘 없는 항목이 옆에 계속 보인다.
    legacy = target_dir / "LeetKit Manager"
    if legacy.is_symlink():
        legacy.unlink(missing_ok=True)

    return app_path


def has_shortcut_been_offered() -> bool:
    """이미 한 번(위치 선택이든 기본값이든) 바로가기 생성을 시도했는지 — 온보딩 마법사가
    이 단계를 다시 보여줄지 판단하는 데 쓴다."""
    return _MARKER.exists()


def mark_shortcut_offered(target_dir: Path | None = None) -> None:
    """어디에 만들었는지도 같이 적는다. 나중에 바로가기를 고쳐야 할 때(맥의 .app 전환
    같은) 사용자가 고른 폴더를 찾아갈 수 있어야 한다 — 예전엔 "1"만 적어서 바탕화면이
    아닌 곳에 만든 사람은 찾을 방법이 없었다."""
    _MARKER.parent.mkdir(parents=True, exist_ok=True)
    _MARKER.write_text(str(target_dir) if target_dir else "1", encoding="utf-8")


def recorded_shortcut_dir() -> Path | None:
    try:
        recorded = _MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not recorded or recorded == "1":  # 위치를 안 적던 시절의 표시
        return None
    return Path(recorded)


def _shortcut_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("LeetKit Manager.lnk",)
    if sys.platform == "darwin":
        return ("LeetKit Manager.app", "LeetKit Manager")  # 두 번째는 옛 심볼릭 링크
    return ("LeetKit Manager",)


def existing_shortcut() -> Path | None:
    """실제로 남아 있는 바로가기. 없으면 None.

    "물어본 적 있다"는 표시만으로 건너뛰면, 사용자가 바로가기를 지웠거나 생성이 실제로는
    실패한 경우에 다시 만들 방법이 사라진다 — 마법사는 표시를 보고 건너뛰고, 다른
    진입점은 없다. 표시가 아니라 파일이 있는지로 판단할 수 있게 한다."""
    for target_dir in [d for d in (recorded_shortcut_dir(), Path.home() / "Desktop") if d]:
        for name in _shortcut_names():
            try:
                candidate = target_dir / name
                if candidate.exists() or candidate.is_symlink():
                    return candidate
            except OSError:
                continue
    return None


def migrate_macos_shortcut() -> Path | None:
    """예전 버전이 만든 심볼릭 링크 바로가기를 .app 번들로 갈아끼운다.

    바로가기는 온보딩에서 한 번만 만들고 그 뒤로는 "이미 물어봤다"는 표시 때문에 다시
    안 만든다 — 업데이트만으로는 이미 깔린 링크가 그대로 남아, 고쳐놓은 아이콘·이름·
    터미널 문제가 정작 기존 사용자에게는 하나도 안 닿는다.

    사용자가 고른 폴더를 먼저 보고, 없으면 바탕화면을 본다. 링크가 없으면(이미
    번들이거나 애초에 안 만들었으면) 아무것도 안 한다."""
    if sys.platform != "darwin":
        return None
    candidates = [d for d in (recorded_shortcut_dir(), Path.home() / "Desktop") if d]
    for target_dir in candidates:
        try:
            if (target_dir / "LeetKit Manager").is_symlink():
                return create_shortcut_at(target_dir)
        except OSError:
            continue
    return None

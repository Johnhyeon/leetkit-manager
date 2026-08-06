"""pywebview 창 실행 — 8단계(UI) 진입점. orchestrator 로직은 전혀 포함하지 않는다."""

from __future__ import annotations

import sys
from pathlib import Path

import webview

from leetkit_manager import shortcut, single_instance
from leetkit_manager.ui.api import Api

_UI_DIR = Path(__file__).parent


def run() -> None:
    if single_instance.is_already_running():
        print("LeetKit Manager가 이미 실행 중입니다 — 기존 창을 확인하세요.", file=sys.stderr)
        return

    single_instance.acquire()
    try:
        shortcut.ensure_desktop_shortcut_once()
        api = Api()
        webview.create_window(
            "LeetKit Manager",
            url=str(_UI_DIR / "index.html"),
            js_api=api,
            width=820,
            height=800,
            min_size=(720, 680),
            background_color="#16181b",
        )
        # 기본값(private_mode=True)은 매 실행마다 프로필을 새로 만들어 localStorage가
        # 초기화된다 — "가이드 최초 1회만" 판단이 매번 리셋되는 원인이었다. 지정 폴더에
        # 프로필을 남겨(private_mode=False) 재실행해도 이어지게 한다.
        storage_path = str(Path.home() / ".leetkit-manager" / "webview")
        icon_path = _UI_DIR / "icon.ico"
        webview.start(
            private_mode=False,
            storage_path=storage_path,
            icon=str(icon_path) if icon_path.exists() else None,
        )
    finally:
        single_instance.release()


if __name__ == "__main__":
    run()

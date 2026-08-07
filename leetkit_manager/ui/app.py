"""pywebview 창 실행 — 8단계(UI) 진입점. orchestrator 로직은 전혀 포함하지 않는다."""

from __future__ import annotations

import sys
from pathlib import Path

import webview

from leetkit_manager import orchestrator, package_service, review_prompt, single_instance
from leetkit_manager.ui.api import Api

_UI_DIR = Path(__file__).parent


def run() -> None:
    if single_instance.is_already_running():
        # 창 없는 exe에선 stderr가 없어 print가 사라진다 — 기존 창을 앞으로 가져오거나
        # 최소한 메시지 상자를 띄운다(single_instance.notify_already_running 참고).
        single_instance.notify_already_running()
        return

    # 지난번 자체 업데이트가 남긴 <exe>.exe.old 정리 — 그때는 이 프로세스가 아직
    # 그 파일명을 쥐고 있어서 못 지웠다(package_service.replace_running_exe 참고).
    package_service.cleanup_old_exe_backup()

    single_instance.acquire()
    # 후기 요청 시점 판단용(첫 실행이 언제였는지·몇 번 열었는지). 중복 실행으로 되돌아간
    # 경우는 위에서 이미 return했으므로 여기 오는 것만 실제 실행이다. 후기 요청 하나
    # 때문에 앱이 안 뜨면 안 되므로 어떤 예외도 삼킨다.
    try:
        review_prompt.record_launch()
    except Exception:
        pass
    try:
        # 바로가기 생성은 더 이상 여기서 자동으로 하지 않는다 — 위치 선택 다이얼로그가
        # 필요한데(webview.start()가 실제로 창을 띄운 뒤에만 호출 가능) 이 시점엔 아직
        # 창도 없다. 대신 Api.choose_shortcut_location()이 창이 뜬 뒤(pywebviewready)
        # 온보딩 마법사 0단계에서 호출된다 — shortcut.has_shortcut_been_offered()로
        # "물어본 적 있는지"만 JS가 확인해 마법사를 보여줄지 판단한다.
        api = Api()
        window = webview.create_window(
            "LeetKit Manager",
            url=str(_UI_DIR / "index.html"),
            js_api=api,
            # MCP 등록 대상이 Codex까지 셋으로 늘면서 "Claude Desktop · Claude Code CLI ·
            # Codex CLI"가 카드 폭을 넘겨 말줄임(…)으로 잘렸다 — 다 보이도록 넓힌다.
            width=1180,
            height=820,
            min_size=(1040, 700),
            background_color="#16181b",
        )
        # 텔레그램 로그인 중에 창을 X로 닫으면 자식 프로세스가 그대로 남아
        # session.session을 계속 쥔다 — 다음 로그인이 SQLite 잠금으로 실패한다.
        # 모달 취소 경로에만 정리가 걸려 있었어서 창 닫기에도 같이 건다.
        window.events.closing += lambda: orchestrator.cancel_telegram_login()
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

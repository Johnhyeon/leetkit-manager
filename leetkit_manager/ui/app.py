"""pywebview 창 실행 — 8단계(UI) 진입점. orchestrator 로직은 전혀 포함하지 않는다."""

from __future__ import annotations

import sys
from pathlib import Path

import webview

from leetkit_manager import orchestrator, package_service, review_prompt, shortcut, single_instance
from leetkit_manager.ui.api import Api

_UI_DIR = Path(__file__).parent


def _apply_macos_app_identity() -> None:
    """macOS 도크·메뉴막대에 뜨는 이름과 아이콘을 잡아준다. 다른 OS에선 아무것도 안 한다.

    .app 번들을 만들어줘도 이것만으로는 부족할 수 있다 — macOS는 "지금 실행 중인
    바이너리가 어느 번들에 들어 있나"로 앱 정체를 판별하는데, 번들 안의 실행 스크립트가
    번들 **밖**의 파이썬을 exec하면 그 파이썬 위치를 기준으로 판별해버린다. 그러면
    번들을 잘 만들어놨어도 도크에는 다시 "Python"이 뜬다(실제로 겪은 증상).

    그래서 번들에 기대지 않고 프로세스 안에서 직접 이름을 박는다. 터미널에서
    `leetkit-manager gui`로 띄운 경우에도 같이 고쳐지는 게 덤이다.

    PyObjC는 macOS의 pywebview가 이미 의존하는 패키지라 별도 설치가 필요 없다.
    실패해도 창은 정상적으로 떠야 하므로 어떤 예외도 삼킨다(이름이 좀 이상한 것보다
    앱이 안 뜨는 게 훨씬 나쁘다)."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle  # type: ignore[import-not-found]

        bundle = NSBundle.mainBundle()
        # localizedInfoDictionary가 있으면 그쪽이 우선 적용된다 — 둘 다 고쳐야 확실하다.
        for info in (bundle.localizedInfoDictionary(), bundle.infoDictionary()):
            if info is not None:
                info["CFBundleName"] = "LeetKit Manager"
                info["CFBundleDisplayName"] = "LeetKit Manager"
    except Exception:
        pass

    try:
        from AppKit import NSApplication, NSImage  # type: ignore[import-not-found]

        icns = _UI_DIR / "icon.icns"
        if icns.exists():
            image = NSImage.alloc().initWithContentsOfFile_(str(icns))
            if image is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:
        pass


def _accept_first_click_on_macos() -> None:
    """macOS에서 버튼을 한 번에 누를 수 있게 한다.

    증상: 맥에서 어떤 버튼이든 한 번 눌러서는 아무 일도 안 일어나고, 두 번 눌러야
    작동한다.

    원인: macOS는 앱이 활성 상태가 아닐 때 들어온 첫 클릭을 "창을 앞으로 가져오기"에
    써버리고 그 아래 컨트롤에는 전달하지 않는다. 네이티브 앱은 뷰가
    `acceptsFirstMouse:`에 YES를 돌려줘서 그 클릭까지 받게 하는데, pywebview의 코코아
    백엔드에는 이 재정의가 아예 없다(설치된 webview/platforms/cocoa.py 확인). 그래서
    창이 활성이 아닐 때마다 한 번씩 헛클릭이 생기고, 사용자 눈에는 "버튼이 안 먹는다"로
    보인다. Windows에는 이 개념 자체가 없어서 같은 코드가 멀쩡히 돈다.

    카테고리로 WKWebView에 그 메서드를 붙인다 — pywebview가 쓰는 뷰가 이 클래스를
    상속하고, 자기 쪽에서 재정의하지 않으므로 이걸로 덮인다.

    실패해도 창은 떠야 한다. 최악의 경우 예전처럼 두 번 눌러야 할 뿐이다.
    """
    if sys.platform != "darwin":
        return
    try:
        import objc  # type: ignore[import-not-found]
        from WebKit import WKWebView  # type: ignore[import-not-found]

        class _FirstMouse(objc.Category(WKWebView)):  # type: ignore[misc]
            def acceptsFirstMouse_(self, event):  # noqa: N802 — ObjC 셀렉터 이름
                return True
    except Exception:
        pass


def run(*, debug: bool = False) -> None:
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

    # 맥에서 예전 버전이 만든 심볼릭 링크 바로가기를 .app 번들로 갈아끼운다. 바로가기는
    # 온보딩에서 한 번만 만들고 그 뒤론 다시 안 만들기 때문에, 이게 없으면 고쳐놓은
    # 아이콘·이름·터미널 문제가 정작 기존 사용자에게는 하나도 안 닿는다.
    try:
        shortcut.migrate_macos_shortcut()
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
            # style.css의 --bg와 같은 값이어야 한다. 페이지가 그려지기 전까지 보이는
            # 색이라, 다르면 창을 열 때·크기를 바꿀 때 한 번씩 다른 색이 번쩍인다
            # (팔레트를 #16181b에서 내렸는데 여기가 안 따라와 있었다).
            background_color="#0d0f12",
            # pywebview 기본값은 False — 화면의 어떤 글자도 드래그로 선택되지 않는다.
            # 지원 문의 화면의 받는사람·제목처럼 사용자가 자기 메일 앱의 각 칸에 옮겨
            # 적어야 하는 값까지 못 집는다("메일 내용 복사"는 셋을 한 덩어리로 주므로
            # 칸마다 나눠 넣을 수가 없다). 켜두고, 앱처럼 보여야 하는 곳은 style.css에서
            # user-select로 다시 잠근다.
            text_select=True,
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
        _apply_macos_app_identity()
        _accept_first_click_on_macos()
        # debug=True면 웹 검사기가 열린다(창에서 우클릭 → 요소 검사). 화면은 그대로인데
        # 버튼만 안 먹는 식의 문제는 자바스크립트 오류가 조용히 삼켜진 경우가 대부분이라,
        # 그 오류를 직접 읽을 방법이 없으면 원인 추측만 하게 된다. 평소엔 꺼둔다.
        webview.start(
            private_mode=False,
            storage_path=storage_path,
            icon=str(icon_path) if icon_path.exists() else None,
            debug=debug,
        )
    finally:
        single_instance.release()


if __name__ == "__main__":
    run()

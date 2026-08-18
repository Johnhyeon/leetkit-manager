"""문제 해결(문의 전에 해볼 것) 흐름이 화면과 실제로 이어져 있는지.

이 창의 가치는 "읽는 안내문"이 아니라 **눌러서 실행되는 순서**라는 데 있다. 그래서
깨지는 방식도 조용하다 — 버튼은 그대로 보이는데 핸들러가 붙는 id가 사라졌거나,
단계가 부르는 API 이름이 바뀌었거나, 결과 줄에 쓰는 클래스에 색이 없거나. 어느 쪽도
화면은 멀쩡해 보이고, 정작 도움이 필요한 사람만 눌러도 아무 일이 없는 걸 겪는다.

JS를 실행할 수 없으므로 정적으로 검사한다. 완벽하진 않지만 "이름이 어긋나는" 종류의
사고는 전부 여기서 걸린다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "leetkit_manager" / "ui"
HTML = (UI / "index.html").read_text(encoding="utf-8")
JS = (UI / "app.js").read_text(encoding="utf-8")
CSS = (UI / "style.css").read_text(encoding="utf-8")
API = (UI / "api.py").read_text(encoding="utf-8")


def _html_ids() -> set[str]:
    return set(re.findall(r'id="([^"]+)"', HTML))


class TestWiring:
    @pytest.mark.parametrize(
        "element_id",
        [
            "troubleshoot-btn",       # 상단 진입 버튼
            "troubleshoot-backdrop",  # 창 자체
            "troubleshoot-steps",     # 단계가 그려지는 자리
            "troubleshoot-close",
            "troubleshoot-support",   # 마지막 손잡이 — 지원 문의로 넘어간다
        ],
    )
    def test_element_exists_and_js_uses_it(self, element_id: str):
        assert element_id in _html_ids(), f"index.html에 #{element_id}가 없습니다"
        assert f'getElementById("{element_id}")' in JS, f"app.js가 #{element_id}를 안 씁니다"

    def test_entry_button_sits_next_to_support(self):
        """문의하려고 마음먹은 사람의 눈이 지나가는 자리여야 한 번이라도 눌러본다.
        멀리 떨어뜨리면 이 창은 있으나 마나가 된다."""
        i_trouble = HTML.index('id="troubleshoot-btn"')
        i_support = HTML.index('id="support-btn"')
        assert i_trouble < i_support, "문제 해결 버튼은 지원 문의 왼쪽에 있어야 합니다"

    def test_last_step_hands_off_to_support(self):
        assert "openSupportModal()" in JS, "끝까지 안 되면 문의로 이어져야 합니다"


class TestSteps:
    def test_steps_are_declared(self):
        assert "TROUBLESHOOT_STEPS" in JS

    @pytest.mark.parametrize("key", ["restart", "update", "connect"])
    def test_step_present(self, key: str):
        assert f'key: "{key}"' in JS, f"{key} 단계가 사라졌습니다"

    def test_restart_step_is_first(self):
        """접수된 문의에서 가장 자주 끝나는 단계다. 순서가 곧 안내이므로 위에 있어야 한다."""
        assert JS.index('key: "restart"') < JS.index('key: "update"') < JS.index('key: "connect"')

    @pytest.mark.parametrize(
        "method",
        [
            # 호스트 앱이 Claude Desktop 하나가 아니게 되면서(ChatGPT 앱이 같은
            # ~/.codex/config.toml 을 읽는다) 묶음 API로 바뀐 자리다.
            "running_host_apps",
            "installed_host_apps",
            "launch_host_apps",
            "restart_host_apps",
            "diagnose",
        ],
    )
    def test_called_api_actually_exists(self, method: str):
        """이름이 어긋나면 버튼을 눌러도 조용히 아무 일도 안 일어난다."""
        assert f"api.{method}(" in JS, f"단계가 api.{method}를 안 부릅니다"
        assert f"def {method}(" in API, f"api.py에 {method}가 없습니다"

    def test_connection_step_goes_online(self):
        """실제로 시세를 한 번 불러봐야 "데이터가 들어오는가"를 답할 수 있다.
        online=False로 돌리면 설치·라이선스만 보고 "정상"이라고 말하게 된다 —
        2026-08-13 문의에서 지원 번들 요약이 정확히 그렇게 거짓말했다."""
        assert "api.diagnose(true)" in JS


class TestResultStyling:
    @pytest.mark.parametrize("status", ["ok", "warn", "fail"])
    def test_result_state_has_a_color(self, status: str):
        """결과 줄이 전부 같은 색이면 성공과 실패가 구분이 안 된다."""
        assert f".troubleshoot-step-result.{status}" in CSS

    def test_result_keeps_line_breaks(self):
        """Lens마다 실패 사유가 한 줄씩 붙는다. 줄바꿈이 죽으면 한 덩어리로 뭉친다.

        셀렉터가 여러 규칙에 등장하므로(예: user-select 묶음) 위치로 자르지 않고
        `.troubleshoot-step-result { ... }` 규칙 본문만 꺼내 본다."""
        m = re.search(r"\.troubleshoot-step-result\s*\{([^}]*)\}", CSS)
        assert m, "규칙 자체가 없습니다"
        assert "pre-wrap" in m.group(1)


class TestFailureGuidance:
    """실패했을 때 "무엇이 잘못됐나"만 말하고 "무엇을 하면 되나"를 빼면, 사용자는
    결국 우리에게 물어야 한다 — 이 창을 만든 이유가 사라진다.

    진단 항목에는 summary(무엇이)와 action(무엇을 하면)이 둘 다 정의돼 있다."""

    def test_shows_what_to_do_not_just_what_broke(self):
        assert "c.summary" in JS
        assert "c.action" in JS, "해결 방법(action)을 화면에 안 씁니다"

    def test_points_at_the_repair_button_when_one_exists(self):
        assert "repairable_repair_id" in JS

    def test_falls_back_when_no_check_failed(self):
        """overall은 fail인데 개별 항목엔 fail이 없는 경우가 있다(시간 초과·버전 불일치).
        그때 아무 줄도 안 남기면 "실패했다"만 알고 이유는 모른다."""
        assert "problem_detail" in JS


class TestManagerAppearsInTheUpdateNotice:
    """Manager 업데이트는 상단의 작은 [업데이트] 버튼으로만 알렸다 — 그게 있는 줄도
    모르고 몇 버전을 건너뛰는 사람이 생긴다. Lens를 업데이트하러 여는 그 창이
    가장 자연스러운 자리다."""

    def test_notice_lists_the_manager_too(self):
        assert "selfUpdateHasUpdate()" in JS
        assert "LeetKit Manager</span>" in JS, "업데이트 목록에 Manager 줄이 없습니다"

    def test_manager_version_is_part_of_the_dismiss_signature(self):
        """빼두면 Lens는 그대로인데 Manager만 새로 나온 날, 예전에 누른 '나중에'가
        그대로 살아 있어 안내가 아예 안 뜬다."""
        m = re.search(r"function updateNoticeSignature\([^)]*\)\s*\{(.+?)\n\}", JS, re.S)
        assert m, "함수를 찾지 못했습니다"
        assert "manager@" in m.group(1)

    def test_manager_is_updated_last(self):
        """먼저 하면 앱이 자기를 바꿔치고 다시 시작해버려서 Lens 업데이트가 시작도
        못 한다 — 사용자에겐 '눌렀는데 Lens는 그대로'로 보인다."""
        m = re.search(r'getElementById\("update-now"\)\.addEventListener\((.+?)\n\}\);', JS, re.S)
        assert m, "핸들러를 찾지 못했습니다"
        body = m.group(1)
        assert body.index("runLensUpdatesFromNotice") < body.index("runSelfUpdate")

    def test_both_entry_points_share_one_path(self):
        """상단 버튼과 시작 안내가 각자 구현을 가지면 한쪽만 고쳐진다."""
        assert "async function runSelfUpdate(" in JS
        assert JS.count("api.self_update()") == 1


class TestGuideTourCoversIt:
    """가이드 투어에 없으면 버튼이 있는 줄도 모르는 사람이 생긴다 — 상단 버튼 중
    이것만 설명이 빠지면 오히려 눈에 덜 띈다."""

    def test_tour_has_a_step_for_the_button(self):
        assert 'selector: "#troubleshoot-btn"' in JS

    def test_tour_order_matches_the_topbar(self):
        """설명을 듣고 눈을 들었을 때 그 자리에 있어야 한다.
        "문제 해결 → 그래도 안 되면 지원 문의" 라는 순서 자체가 안내다."""
        assert JS.index('selector: "#patchnotes-btn"') \
            < JS.index('selector: "#troubleshoot-btn"') \
            < JS.index('selector: "#support-btn"')

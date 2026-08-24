"""등록 모달의 **기본 체크**가 ChatGPT 만 쓰는 사람에게도 맞는지.

codex 는 기본 체크에서 빼왔다 — `~/.codex` 만 있는 사람(개발 도구를 깔아둔 경우)까지
안 쓰는 곳에 등록해버리기 때문이다. 그런데 Claude 가 하나도 없는 사람에게는 그게 유일
하게 쓸 수 있는 항목인데 꺼져 있어서, "다음"만 누르면 아무 데도 등록되지 않은 채로
마법사가 끝났다. ChatGPT 만 쓰는 고객의 첫 5분이 여기서 무너진다.

JS를 실행할 수 없으므로 규칙이 코드에 남아 있는지 정적으로 확인한다.
"""

from __future__ import annotations

from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "leetkit_manager" / "ui"
JS = (UI / "app.js").read_text(encoding="utf-8")


def test_codex_is_prechecked_when_no_claude_host_is_available():
    assert "const claudeAvailable = targets.some((t) => t.id !== \"codex\" && t.installed);" in JS
    # 기본 체크 조건에 !claudeAvailable 이 들어가 있어야 한다.
    assert "|| !claudeAvailable" in JS


def test_codex_is_not_prechecked_for_claude_users():
    """Claude 를 쓰는 사람의 **첫 등록**에서는 codex 체크가 꺼져 있어야 한다 —
    안 쓰는 곳에 등록되면 그쪽 앱이 뜰 때마다 도구가 늘어난다.
    (이미 등록된 사람은 아래 test_register_modal_shows_actual_registration 규칙을 탄다.)"""
    assert 't.id !== "codex" || !claudeAvailable' in JS


def test_register_modal_shows_actual_registration():
    """등록된 Lens 의 등록 창은 "지금 상태"를 그대로 보여야 한다.

    실사용 버그: Claude Desktop 등록을 해제하고 창을 다시 열면 체크가 그대로 켜져 있어
    해제가 안 된 것처럼 보였다(설정 파일에서는 이미 지워진 뒤였다 — 화면만 거짓말).
    원인은 "설치돼 있으면 무조건 체크"였던 기본값이다."""
    assert "const alreadyRegistered = currentTargets.length > 0;" in JS
    assert "? currentTargets.includes(t.id)" in JS
    # 체크박스 옆에 지금 연결 상태를 글자로도 보여준다(체크박스를 설치 여부로 오해하지 않게).
    assert '" (지금 등록돼 있음)"' in JS


def test_unregistering_everything_is_allowed():
    """전부 체크를 푼 것은 "전부 해제"다. 예전엔 "하나 이상 선택하세요"로 막혀서
    화면에서 전체 해제를 할 방법이 없었다(백엔드는 원래 이 경우를 처리한다)."""
    assert "if (!beforeTargets.length) {" in JS
    assert "연결을 모두 해제할까요" in JS


def test_restart_prompt_follows_what_actually_changed():
    """등록 후 안내는 **바뀐 타겟**만 보고 만들어야 한다.

    실사용 버그: Claude Desktop 등록을 해제했는데 "ChatGPT 를 다시 시작하라"고 떴다.
    체크된 목록(checked)만 보고 안내를 만들었기 때문이다 — 해제한 쪽은 목록에서 빠지고,
    그대로 남아 있던(=아무것도 안 바뀐) ChatGPT 가 안내 대상이 됐다."""
    assert "const relevantHostIds = checked.map" not in JS
    assert "const beforeTargets = ((lensDataCache[lensName] || {}).targets || []).slice();" in JS
    assert "const addedTargets = onboardingActive ? [] : checked.filter((t) => !beforeTargets.includes(t));" in JS
    assert "const removedTargets = onboardingActive ? [] : result.removed || [];" in JS
    assert "function registrationChangeNotes(changedTargets, runningHosts, kind) {" in JS
    # 해제 쪽 문구가 따로 있어야 한다 — "나타납니다"만 있으면 해제에도 그 말이 나간다.
    # 해제는 단정하지 않는다: 호스트 앱이 이미 목록에서 뺐을 수 있다.
    assert "껐다 켜야 도구가 나타납니다" in JS
    assert "에 아직 도구가 남아 있으면 껐다 켜주세요" in JS


def test_restart_lets_you_choose_which_app():
    """켜져 있는 앱이 둘일 때 한 번에 둘 다 끄지 않는다 — 손대지도 않은 앱이 같이
    닫히는 건 시킨 적 없는 종료다(한쪽에서 대화 중일 수 있다)."""
    assert "function renderRestartTargets(apps) {" in JS
    assert "function restartModalSelectedIds() {" in JS
    assert "const ids = restartModalSelectedIds();" in JS
    # 상단 [다시 시작] 버튼도 확인창 하나로 전부 끄던 걸 고르기로 바꿨다.
    assert "껐다 다시 켤까요" not in JS
    # 등록 모달의 재시작 버튼은 앱마다 하나씩 만든다.
    assert "restartApps.forEach((app) => {" in JS

    html = (UI / "index.html").read_text(encoding="utf-8")
    assert 'id="restart-targets"' in html
    assert 'id="restart-pick-label"' in html

    # .register-targets 의 display:flex 가 [hidden] 을 이긴다 — 명시적으로 눌러줘야
    # 앱이 하나뿐일 때 목록이 감춰진다.
    css = (UI / "style.css").read_text(encoding="utf-8")
    assert ".register-targets[hidden]" in css


def test_lens_restart_prompt_targets_only_the_apps_it_runs_on():
    """Lens 하나를 업데이트·삭제한 뒤의 재시작 안내는 **그 Lens가 물려 있는 앱**만
    지목해야 한다.

    예전엔 "물려 있나"를 참/거짓으로만 받고 실제 대상은 "지금 켜져 있는 앱 전부"로
    잡았다 — StockLens만 업데이트했는데 옆에 켜둔 ChatGPT까지 껐다 켜라고 했고,
    그 앱은 StockLens를 띄운 적도 없다(등록 모달에서 고친 것과 같은 부류)."""
    assert "function hostIdsForTargets(targets) {" in JS
    assert "async function noteLensFilesChanged({ hostAppIds, headline, afterRestart, whenClosedResult }) {" in JS
    assert "registeredOnHostApp" not in JS
    assert "const running = (await runningHostApps()).filter((a) => ids.includes(a.id));" in JS
    # 빈 배열이 "대상 없음"으로 읽혀야 한다 — `apps || …` 였을 때는 빈 배열이 falsy라
    # 켜져 있는 앱 전부로 조용히 되돌아갔다.
    assert "const running = Array.isArray(apps) ? apps : await runningHostApps();" in JS


def test_relaunch_is_only_claimed_when_it_worked():
    """"다시 켰습니다"는 실제로 켜졌을 때만 할 말이다 — 우리가 껐으므로, 못 켠 채로
    그렇게 말하면 사용자는 앱이 꺼진 줄도 모르고 "왜 안 보이지"를 겪는다."""
    assert "const relaunch = await window.pywebview.api.launch_host_apps(ids);" in JS
    assert "relaunch && relaunch.ok" in JS


def test_onboarding_finish_names_only_registered_apps():
    """마법사 마지막 안내도 같은 규칙 — 등록한 곳만 말한다. 그리고 허용 창을 띄우는
    앱 이름도 등록한 곳에서 가져온다(ChatGPT만 쓰는 사람에게 "Claude가 물어봅니다"는
    자기 얘기가 아니다)."""
    assert "const registeredHostIds = [" in JS
    assert "(await runningHostApps()).filter((a) => registeredHostIds.includes(a.id))" in JS
    assert "처음 도구를 쓸 때 Claude가 허용 여부를 물어봅니다" not in JS
    assert "${permissionApp}이(가) 허용 여부를 물어봅니다" in JS


def test_host_app_targets_are_mapped_for_restart_prompts():
    """codex 타겟은 ChatGPT 앱을 가리킨다 — 이 표가 없으면 등록·업데이트 후 재시작
    안내가 ChatGPT 사용자에게 가지 않는다."""
    assert 'const TARGET_HOST_APP = { "claude-desktop": "claude-desktop", codex: "chatgpt" };' in JS


def test_codex_check_has_a_customer_facing_label():
    """진단 항목 식별자는 고객 언어로 바꿔서 보여준다. codex 가 빠져 있어서
    ChatGPT 에 등록한 DartLens·TelegramLens 사용자는 카드에서 `MCP_CONFIG_CODEX`
    라는 원본 식별자를 그대로 봤다."""
    assert 'MCP_CONFIG_CODEX: "ChatGPT (Codex) 등록"' in JS
    # 조치 흐름 연결은 원래 있었다 — 라벨만 빠져 있었다.
    assert "MCP_CONFIG_CODEX: \"register\"" in JS


def test_fresh_install_from_card_leads_into_registration():
    """마법사는 설치 다음에 등록을 물어보는데, 마법사를 끝낸 사람이 카드에서 설치하면
    "설치 완료" 토스트만 뜨고 끝났다 — 설치는 됐는데 도구가 안 보이는 자리."""
    assert "openRegisterModal(lensName);" in JS
    # 새 설치 + 등록된 곳 없음 + 마법사 밖 일 때만 이어준다(업데이트에는 안 뜬다).
    assert "!wasInstalled &&" in JS
    assert "!onboardingActive &&" in JS
    assert "!(((lens && lens.targets) || []).length)" in JS


def test_update_refreshes_the_registered_command_path():
    """설정 파일에는 등록 당시의 실행 파일 경로가 박힌다. 그게 옛 위치를 가리키면
    새 버전을 받아도 호스트 앱은 계속 옛 것을 띄운다 — 업데이트 뒤 같은 대상으로 다시
    등록해서 경로만 갱신한다(대상을 늘리거나 줄이지 않는다)."""
    assert "const registered = (lens && lens.targets) || [];" in JS
    assert "if (wasInstalled && registered.length) {" in JS
    assert "api.register(lensName, registered)" in JS


def test_guide_tour_says_where_lenses_run():
    """[가이드] 버튼은 버튼을 하나씩 짚는 구성이라, 정작 Lens 가 어느 앱 위에서 도는지
    말하는 자리가 없었다 — Claude 하나뿐일 때는 필요 없었지만 이제 둘이다."""
    assert "Lens는 Claude Desktop · Claude Code · ChatGPT" in JS
    assert "어디에 연결해도 똑같이 동작합니다" in JS


def test_app_names_are_written_as_product_names():
    """Claude 는 `Claude Desktop` 처럼 제품명 그대로 쓰면서 ChatGPT 만
    "ChatGPT 데스크탑 앱" 으로 영문+한글이 섞여 있었다. OpenAI 는 "ChatGPT Desktop"
    이라는 제품명을 쓰지 않는다 — 앱 이름이 그냥 ChatGPT 다."""
    assert "ChatGPT 데스크탑" not in JS


def test_refresh_offers_bulk_update_when_several_are_stale():
    """카드에서 하나씩 세 번 누르는 게 번거롭다 — 일괄 처리 창이 이미 있는데
    [진단 재실행] 경로에서는 안 띄우고 있었다. 하나뿐일 때는 안 띄운다(카드 버튼
    한 번이면 끝나는 일에 창을 겹치는 게 더 번거롭다)."""
    assert "const UPDATE_NOTICE_MIN_LENSES = 2;" in JS
    assert "lensesWithUpdates().length >= UPDATE_NOTICE_MIN_LENSES" in JS
    assert "maybeShowUpdateNotice({ afterUserAction: true })" in JS


def test_later_choice_is_still_respected_on_refresh():
    """force 로 자제 규칙을 전부 풀면 "나중에"를 눌러도 진단할 때마다 다시 뜬다."""
    assert "if (!force && !afterUserAction) {" in JS

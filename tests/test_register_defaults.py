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
    """Claude 를 쓰는 사람에게는 예전 규칙(이미 등록된 경우만 체크)이 그대로여야 한다 —
    안 쓰는 곳에 등록되면 그쪽 앱이 뜰 때마다 도구가 늘어난다."""
    assert 't.id !== "codex" || currentTargets.includes(t.id)' in JS


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

"""고객 화면에 터미널 명령이 새지 않는지, Lens 진단의 새 항목을 제대로 받는지.

대표 결정(2026-09-17, 토스 UX 조사 반영): 고객에게 보이는 곳에 터미널 명령을 쓰지
않는다. 할 일은 Manager 버튼으로 안내하고, 명령어·예외 원문은 [결과 복사]·지원 번들로만
나간다. Lens 진단의 action에는 새 Lens의 안내 글과 옛 Lens의 명령어가 한동안 섞여 오므로,
화면이 그 둘을 가려내는 판정(app.js looksLikeCommand)이 이 계약의 중심이다.

JS 동작은 node가 있으면 app.js에서 해당 함수만 떼어 실제로 돌려 본다(없으면 건너뜀).
나머지는 저장소 관례대로 정적으로 검사한다.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import load_fixture

from leetkit_manager import orchestrator, support_bundle
from leetkit_manager.lens_contract import STOCKLENS
from leetkit_manager.models import DoctorReport
from leetkit_manager.process_runner import ProcessResult
from leetkit_manager.ui.api import Api

PKG = Path(__file__).resolve().parent.parent / "leetkit_manager"
UI = PKG / "ui"
JS = (UI / "app.js").read_text(encoding="utf-8")
HTML = (UI / "index.html").read_text(encoding="utf-8")
# 주석 줄을 뺀 코드 — "옛 문구가 없어졌다"는 검사는 왜 바꿨는지 적은 주석에 걸리면 안 된다.
JS_CODE = "\n".join(ln for ln in JS.split("\n") if not ln.strip().startswith(("//", "*", "/*")))

# 사양 4장의 금지 패턴.
FORBIDDEN = re.compile(
    r"\b(stocklens|dartlens|telegramlens)-(activate|doctor|setup|login|broker)\b"
    r"|\buv (tool|pip|run)\b|터미널|PowerShell|@gmail\.com"
    r"|STOCKLENS_HOME|DARTLENS_HOME|TELEGRAMLENS_HOME",
    re.I,
)

# 화면 글자 그대로의 버튼 이름(이름 변경은 대표 결정 대기 중이라 지금 이름만 허용).
BUTTONS = {
    "활성화", "구매", "업데이트", "설치", "재설치", "MCP 등록", "진단", "복구",
    "텔레그램 로그인", "증권사 연결", "지원 문의", "문제 해결",
}

# 옛 Lens가 실제로 내던 action(각 Lens doctor의 fix 문자열) — 화면에서 빠져야 한다.
OLD_COMMAND_ACTIONS = [
    "stocklens-activate <라이선스-키>",
    "stocklens-setup --target both",
    "uv tool install --force stocklens-mcp",
    "dartlens-setup --target {claude-desktop|claude-code|both|codex}",
    "dartlens-setup --plaintext <YOUR_DART_API_KEY>",
    "dartlens-doctor --repair corp-code-cache --yes",
    "telegramlens-login",
    "telegramlens-login (세션 재발급)",
    "Back up and re-run dartlens-setup",
    "구매 시 발송된 키를 다시 정확히 붙여넣어 stocklens-activate로 재활성화하세요.",
    "폴더 권한을 확인하거나 STOCKLENS_HOME 환경변수로 쓰기 가능한 경로를 지정하세요.",
    'Add to PATH: "C:\\Users\\me\\AppData\\Roaming\\Python\\Scripts"',
    "Install uv (recommended):\n  Windows: irm https://astral.sh/uv/install.ps1 | iex",
    "터미널에서 dartlens-setup을 실행해 DART 인증키를 넣으세요",
]

# 새 Lens가 주는 안내 글(사양 2-2·3-2) — 화면에 그대로 보여야 한다.
NEW_GUIDE_ACTIONS = [
    "StockLens 카드의 [활성화]를 눌러 메일로 받은 키를 넣어주세요.",
    "StockLens 카드의 [구매]를 누르고, 받은 키를 [활성화]로 넣어주세요.",
    "착오라면 상단 [지원 문의]를 눌러주세요.",
    "날짜와 시간을 오늘로 맞춘 뒤 [진단]을 다시 눌러주세요.",
    "DartLens가 아직 AI 앱에 등록되지 않았어요.",
    "DartLens 카드의 [MCP 등록]을 눌러주세요.",
    "DartLens 카드의 [활성화]를 눌러 DART 인증키를 넣어주세요.",
    "TelegramLens 카드의 [텔레그램 로그인]을 눌러주세요.",
    "백신이나 회사 보안 프로그램이 연결을 가로채고 있을 수 있어요. StockLens 카드의 [업데이트]를 확인하고, 그래도 같으면 상단 [지원 문의]를 눌러주세요.",
    "인터넷 주소를 찾지 못했어요. 인터넷 연결을 확인한 뒤 [진단]을 다시 눌러주세요. 그래도 같으면 상단 [지원 문의]를 눌러주세요.",
    "StockLens 카드의 [증권사 연결]에서 연결을 다시 확인해 주세요.",
    "Wi-Fi 연결을 확인한 뒤 [진단]을 다시 눌러주세요.",
    "인터넷 연결/방화벽을 확인하세요. 일시적 장애일 수 있습니다.",
]


# ---------------------------------------------------------------------------
# JS 함수 떼어 돌리기
# ---------------------------------------------------------------------------

def _js_decl(name: str) -> str:
    """app.js 최상위 선언 하나(function 또는 const)의 원문."""
    m = re.search(rf"^(?:async )?function {name}\(", JS, re.M)
    if m:
        end = JS.index("\n}\n", m.start()) + 2
        return JS[m.start():end]
    m = re.search(rf"^const {name} = ", JS, re.M)
    assert m, f"app.js에 {name} 선언이 없습니다"
    line_end = JS.index("\n", m.start())
    if JS[m.start():line_end].rstrip().endswith("{"):
        return JS[m.start():JS.index("\n};\n", m.start()) + 3]
    return JS[m.start():line_end + 1]


_RENDER_DECLS = [
    "escapeAttr", "escapeHtml", "EXAMPLE_LENS_DATA", "CHECK_ID_LABEL",
    "SUPPORT_ONLY_DETAIL_CHECKS", "CHECK_RESOLVER", "checkResolverFor",
    "looksLikeCommand", "fallbackCheckGuide", "checkGuideText", "renderCheckItem",
]


@pytest.fixture(scope="module")
def run_js(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("node가 없어 JS 동작 검사는 건너뜁니다")
    prelude = "\n".join(_js_decl(n) for n in _RENDER_DECLS)
    folder = tmp_path_factory.mktemp("js")

    def run(body: str):
        script = folder / "case.js"
        script.write_text(
            prelude + "\nconst __out = (() => {\n" + body + "\n})();\n"
            "console.log(JSON.stringify(__out));\n",
            encoding="utf-8",
        )
        proc = subprocess.run([node, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    return run


def _lens(status: str = "active") -> dict:
    return {"name": "stocklens", "display_name": "StockLens", "license_status": status}


class TestCommandJudgement:
    def test_old_commands_are_recognized(self, run_js):
        got = run_js(f"return {json.dumps(OLD_COMMAND_ACTIONS, ensure_ascii=False)}.map(looksLikeCommand);")
        missed = [a for a, hit in zip(OLD_COMMAND_ACTIONS, got) if not hit]
        assert not missed, f"명령어로 못 알아본 action: {missed}"

    def test_new_guides_are_not_mistaken_for_commands(self, run_js):
        got = run_js(f"return {json.dumps(NEW_GUIDE_ACTIONS, ensure_ascii=False)}.map(looksLikeCommand);")
        wrong = [a for a, hit in zip(NEW_GUIDE_ACTIONS, got) if hit]
        assert not wrong, f"안내 글을 명령어로 잘못 본 action: {wrong}"

    def test_example_in_guide_tour_is_not_a_command(self, run_js):
        assert run_js("return EXAMPLE_LENS_DATA.checks.map((c) => looksLikeCommand(c.action));") == [False]


class TestCheckItemRendering:
    def _render(self, run_js, check: dict, lens: dict) -> str:
        return run_js(
            f"return renderCheckItem({json.dumps(check, ensure_ascii=False)}, {json.dumps(lens, ensure_ascii=False)});"
        )

    @pytest.mark.parametrize("action", OLD_COMMAND_ACTIONS)
    def test_command_action_never_reaches_the_screen(self, run_js, action):
        html = self._render(run_js, {
            "id": "CACHE_WRITABLE", "status": "fail", "summary": "캐시 폴더에 쓸 수 없어요.",
            "action": action, "details": {"lines": ["stocklens-doctor --json", "쓰기 시험 실패"]},
        }, _lens())
        assert "copy-cmd" not in html and "check-action-cmd" not in html
        assert not FORBIDDEN.search(html), html
        assert "--" not in html and "&lt;" not in html, html
        # 명령을 빼고 막다른 길로 두지 않는다 — 할 일은 글로 남는다.
        assert "check-action-guide" in html and "[지원 문의]" in html
        assert "쓰기 시험 실패" in html  # 명령이 아닌 details 줄은 그대로

    def test_korean_guide_is_shown_as_readable_text(self, run_js):
        guide = NEW_GUIDE_ACTIONS[8]
        html = self._render(run_js, {
            "id": "KR_DATA_REACHABLE", "status": "warn", "summary": "국내 데이터 6가지 중 5가지는 정상이에요.",
            "action": guide, "details": {"lines": []}, "critical": False,
        }, _lens())
        assert 'class="check-action check-action-guide"' in html
        assert guide in html
        assert "<button" not in html

    @pytest.mark.parametrize("check_id", ["RECENT_TOOL_FAILURES", "BROKER_DATA_REACHABLE"])
    def test_new_checks_have_labels_and_keep_raw_details_off_screen(self, run_js, check_id):
        html = self._render(run_js, {
            "id": check_id, "status": "warn", "critical": False,
            "summary": "최근 조회 중 아직 실패로 남아 있는 것이 1가지 있어요.",
            "action": "연결이 느려서 시간 안에 답을 못 받았어요. 잠시 뒤 [진단]을 다시 눌러주세요.",
            "details": {"lines": ["get_price: 실패 3번, 마지막 14:02, 분류 timeout, ReadTimeout: timed out"]},
        }, _lens())
        label = {"RECENT_TOOL_FAILURES": "최근 조회 기록", "BROKER_DATA_REACHABLE": "증권사 시세 연결"}[check_id]
        assert f'<span class="check-id">{label}</span>' in html
        assert "ReadTimeout" not in html and "get_price" not in html
        assert 'class="check-item warn"' in html  # 빨강이 아니라 주의색

    @pytest.mark.parametrize(
        "status, resolver",
        [("missing", "activate"), ("invalid", "activate"), ("expired", "activate"), ("revoked", "support")],
    )
    def test_license_button_follows_key_state(self, run_js, status, resolver):
        html = self._render(run_js, {
            "id": "LICENSE_ACTIVE", "status": "fail", "summary": "라이선스 키 확인",
            "action": "stocklens-activate <라이선스-키>", "details": {},
        }, _lens(status))
        assert f'data-resolver="{resolver}"' in html
        if resolver == "support":
            assert ">지원 문의</button>" in html

    def test_clock_has_no_button_only_guidance(self, run_js):
        html = self._render(run_js, {
            "id": "LICENSE_ACTIVE", "status": "fail", "summary": "이 컴퓨터의 날짜가 실제보다 과거로 되어 있어요.",
            "action": "stocklens-activate <라이선스-키>", "details": {},
        }, _lens("clock"))
        assert "<button" not in html
        assert "날짜와 시간을 오늘로 맞춘 뒤 [진단]을 다시 눌러주세요" in html
        assert not FORBIDDEN.search(html)

    def test_manager_guides_use_only_real_button_names(self, run_js):
        texts = run_js("""
            const out = [];
            for (const resolver of [null, "register", "activate", "telegram-login", "repair", "support"])
              for (const id of ["LICENSE_ACTIVE", "DART_API_KEY", "MCP_CONFIG_VALID", "CACHE_WRITABLE"])
                for (const st of ["active", "missing", "expired", "revoked", "clock"])
                  out.push(fallbackCheckGuide({ id }, { display_name: "StockLens", license_status: st }, resolver));
            return out;
        """)
        names = {n for t in texts for n in re.findall(r"\[([^\]]+)\]", t)}
        assert names and names <= BUTTONS, names - BUTTONS
        assert not any(FORBIDDEN.search(t) for t in texts)
        # 앱 문구 규칙(대시·마침표 없음)과 해요체
        assert not any("." in t or "—" in t or "습니다" in t for t in texts)


def test_resolve_check_opens_support_for_revoked_keys():
    body = re.search(r"async function resolveCheck\(.+?\n\}\n", JS, re.S).group(0)
    assert 'resolver === "support"' in body and "openSupportModal()" in body


# ---------------------------------------------------------------------------
# 문구가 바뀐 자리들(정적)
# ---------------------------------------------------------------------------

class TestScreensThatUsedToShowCommands:
    def test_copy_command_ui_is_gone(self):
        assert "copy-cmd" not in JS_CODE
        assert "명령어가 복사되었습니다" not in JS_CODE

    def test_troubleshoot_connect_step_uses_the_same_judgement(self):
        step = re.search(r'key: "connect",(.+?)\n  \},\n\];', JS, re.S).group(1)
        assert "${c.action}" not in step
        assert "checkGuideText(c, l)" in step
        assert "[고치기]" not in JS_CODE
        assert "[복구]" in step

    def test_troubleshoot_connect_step_reports_warnings_too(self):
        """이 단계는 '데이터가 들어오는가'를 묻는다. 일부만 실패한 Lens(주의)를 빼고
        '모두 정상'이라고 하면 새 진단 항목(최근 조회 실패 등)이 여기서 안 보인다."""
        step = re.search(r'key: "connect",(.+?)\n  \},\n\];', JS, re.S).group(1)
        assert 'c.status === "warn"' in step

    def test_install_failure_toast_has_no_rollback_command(self):
        assert "이전 버전 복구" not in JS_CODE
        assert "rollback_command" not in JS_CODE
        assert "그래도 같으면 상단 [지원 문의]를 눌러주세요" in JS_CODE

    def test_tour_describes_the_resolve_button_not_commands(self):
        assert "굵은 명령어" not in JS_CODE
        tour = JS[JS.index('title: "문제 자세히 보기"'):JS.index('title: "동작 버튼"')]
        assert "[지금 해결하기]" in tour

    def test_new_check_labels_exist(self):
        table = _js_decl("CHECK_ID_LABEL")
        assert 'RECENT_TOOL_FAILURES: "최근 조회 기록"' in table
        assert 'BROKER_DATA_REACHABLE: "증권사 시세 연결"' in table


class TestBrokerUpdateRequiredCopy:
    def test_points_at_real_buttons(self):
        proc = ProcessResult(cmd=["stocklens-broker"], exit_code=None, stdout="", stderr="",
                             timed_out=False, duration_s=0.1, error="not_found")
        with patch.object(orchestrator, "run_json_cli", return_value=(proc, None)):
            result = orchestrator.broker_action(STOCKLENS, "status")
        assert "[지금 업데이트]" not in result.message
        assert "StockLens 카드의 [업데이트]" in result.message
        assert "상단 [지원 문의]" in result.message
        assert "습니다" not in result.message  # 한 메시지 안에서 문체를 섞지 않는다


# ---------------------------------------------------------------------------
# 새 진단 항목이 카드·상단 요약을 부풀리지 않는가 (Python 판정)
# ---------------------------------------------------------------------------

def _process() -> ProcessResult:
    return ProcessResult(cmd=["x"], exit_code=0, stdout="", stderr="", timed_out=False, duration_s=0.1)


def _report_with(check: dict, overall: str = "degraded") -> DoctorReport:
    payload = load_fixture("stocklens_doctor.json")
    for c in payload["checks"]:
        c["status"] = "ok"
    payload["overall"] = overall
    payload["checks"].append(check)
    return DoctorReport.from_json(payload)


class TestNonCriticalWarnStaysAtCaution:
    @pytest.mark.parametrize("check_id", ["RECENT_TOOL_FAILURES", "BROKER_DATA_REACHABLE"])
    def test_warn_is_caution_not_action_needed(self, check_id):
        # 세 Lens 모두 warn만 있으면 overall을 degraded로 낸다.
        report = _report_with({"id": check_id, "status": "warn", "summary": "s", "critical": False})
        diag = orchestrator.LensDiagnosis(lens=STOCKLENS, report=report, process=_process())
        assert diag.readiness == "주의"
        assert orchestrator.has_actionable_problem(diag) is False
        assert orchestrator.summarize([diag]) == {"total": 1, "ok": 0, "update_available": 0, "action_needed": 0}

    def test_critical_fail_meaning_is_unchanged(self):
        report = _report_with({"id": "LICENSE_ACTIVE", "status": "fail", "summary": "s", "critical": True},
                              overall="fail")
        diag = orchestrator.LensDiagnosis(lens=STOCKLENS, report=report, process=_process())
        assert diag.readiness == "조치 필요"
        assert orchestrator.has_actionable_problem(diag) is True


# ---------------------------------------------------------------------------
# 화면에서 뺀 원문이 지원 쪽에는 닿는가
# ---------------------------------------------------------------------------

_SUPPORT_CHECK = {
    "id": "RECENT_TOOL_FAILURES", "status": "warn", "critical": False,
    "summary": "최근 조회 중 아직 실패로 남아 있는 것이 1가지 있어요.",
    "action": "stocklens-doctor --online",
    "details": {"lines": ["get_price: 실패 3번, 분류 tls, SSLError: CERTIFICATE_VERIFY_FAILED"]},
}


def test_copy_result_text_carries_action_and_details():
    diag = orchestrator.LensDiagnosis(lens=STOCKLENS, report=_report_with(_SUPPORT_CHECK), process=_process())
    with patch.object(orchestrator, "diagnose_lens", return_value=diag):
        text = Api().diagnostic_text("stocklens")
    assert "조치: stocklens-doctor --online" in text
    assert "SSLError: CERTIFICATE_VERIFY_FAILED" in text


def test_support_bundle_summary_carries_action_and_details():
    diag = orchestrator.LensDiagnosis(lens=STOCKLENS, report=_report_with(_SUPPORT_CHECK), process=_process())
    with patch.object(support_bundle.orchestrator, "run_full_diagnosis", return_value=[diag]):
        text = support_bundle._summary_text([], [])
    assert "조치: stocklens-doctor --online" in text
    assert "SSLError: CERTIFICATE_VERIFY_FAILED" in text


# ---------------------------------------------------------------------------
# 고객 화면 문자열 전수 금지 패턴 검사
# ---------------------------------------------------------------------------

_JS_STRINGS = re.compile(r'"(?:[^"\\\n]|\\.)*"' r"|'(?:[^'\\\n]|\\.)*'" r"|`(?:[^`\\\n]|\\.)*`")


def test_no_forbidden_text_in_app_js_strings():
    bad = []
    for lineno, line in enumerate(JS.split("\n"), start=1):
        if line.strip().startswith(("//", "*", "/*")):
            continue  # 주석은 고객이 안 본다
        for m in _JS_STRINGS.finditer(line):
            if FORBIDDEN.search(m.group(0)):
                bad.append(f"{lineno}행 {m.group(0)[:80]}")
    assert not bad, "app.js 화면 문자열에 금지 표현: " + "; ".join(bad)


def test_no_forbidden_text_in_index_html():
    visible = re.sub(r"<!--.*?-->", "", HTML, flags=re.S)
    bad = [ln.strip()[:80] for ln in visible.split("\n") if FORBIDDEN.search(ln)]
    assert not bad, "index.html에 금지 표현: " + "; ".join(bad)


# 예외 — 고객 화면에 글로 뜨지 않는 문자열. (파일, 값) 쌍으로 명시한다.
_PY_EXCEPTIONS = {
    # update_lens의 rollback_command. API 결과에만 실리고 화면은 싣지 않는다(app.js 설치 실패 알림).
    ("orchestrator.py", "uv tool install --force "),
}
# 명령 이름·환경변수 이름 그 자체(subprocess 인자, os.environ 조회, lens_contract)는 글이 아니다.
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def test_no_forbidden_text_in_python_messages():
    bad = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docs:
                continue
            value = node.value
            if not FORBIDDEN.search(value) or _IDENTIFIER.match(value):
                continue
            if (path.name, value) in _PY_EXCEPTIONS:
                continue
            bad.append(f"{path.relative_to(PKG)}:{node.lineno} {value[:80]!r}")
    assert not bad, "Python 문자열에 금지 표현(예외 목록에 없음): " + "; ".join(bad)

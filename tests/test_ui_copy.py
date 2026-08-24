"""화면에 나가는 문구 규칙.

대표 지시(2026-08-24): 앱 문구에서 em dash(—)와 문장 마침표를 쓰지 않는다.
설명을 늘어놓지 말고 무엇을 하는 기능인지만 남긴다. 문구는 손댈 때마다 하나씩
새는 종류라, 사람이 아니라 여기서 막는다.

코드 주석은 대상이 아니다(고객이 보지 않는다). 검사 대상은 한글이 들어 있는
문자열 리터럴과 index.html 의 눈에 보이는 텍스트뿐이다.
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "leetkit_manager" / "ui"

# 문자열 리터럴 세 종류. 줄 단위로 훑으므로 여러 줄에 걸친 템플릿 리터럴은
# 각 줄이 따로 잡히는데, 규칙 검사에는 그걸로 충분하다.
_STRINGS = re.compile(
    r'"(?:[^"\\\n]|\\.)*"' r"|'(?:[^'\\\n]|\\.)*'" r"|`(?:[^`\\\n]|\\.)*`"
)
_HANGUL = re.compile(r"[가-힣]")
# 한글·닫는 괄호·따옴표 뒤에 붙은 마침표만 문장 마침표로 본다.
# 버전(v0.3.2)·주소(my.telegram.org)·소수점은 걸리지 않는다.
_SENTENCE_DOT = re.compile(r"[가-힣\)\]\"']\.(?:$|\s|\\n)")


def _js_strings() -> list[tuple[int, str]]:
    src = (UI / "app.js").read_text(encoding="utf-8")
    found = []
    for lineno, line in enumerate(src.split("\n"), start=1):
        if line.strip().startswith(("//", "*", "/*")):
            continue  # 주석은 사람이 읽는 글이라 규칙 밖
        for m in _STRINGS.finditer(line):
            literal = m.group(0)
            if _HANGUL.search(literal):
                found.append((lineno, literal[1:-1]))
    return found


def test_no_em_dash_in_app_strings():
    bad = [(n, s) for n, s in _js_strings() if "—" in s or "–" in s]
    assert not bad, "화면 문구에 대시가 남아 있습니다: " + "; ".join(
        f"{n}행 {s[:60]}" for n, s in bad
    )


def test_no_sentence_period_in_app_strings():
    bad = [(n, s) for n, s in _js_strings() if _SENTENCE_DOT.search(s)]
    assert not bad, "화면 문구에 마침표가 남아 있습니다: " + "; ".join(
        f"{n}행 {s[:60]}" for n, s in bad
    )


def _html_visible() -> list[str]:
    html = (UI / "index.html").read_text(encoding="utf-8")
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)  # 주석 제외
    return [ln for ln in html.split("\n") if re.search(r"[가-힣][^<>]*[.—–]", ln)]


def test_no_em_dash_or_period_in_html_text():
    bad = _html_visible()
    assert not bad, "index.html 화면 텍스트에 대시·마침표가 남아 있습니다: " + "; ".join(
        ln.strip()[:60] for ln in bad
    )

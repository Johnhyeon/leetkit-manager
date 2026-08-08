"""PATCHNOTES.md 파서 — 구매자가 앱에서 그대로 읽는 화면이라, 손으로 쓰는 파일의
사소한 어긋남(대시 종류, 빈 줄, 주석) 때문에 내용이 통째로 사라지면 안 된다."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from leetkit_manager import patch_notes

# 네 제품의 실제 파일. 리포가 나란히 있는 개발 환경에서만 전부 검사하고,
# 없으면(리포 하나만 받은 경우) 있는 것만 본다.
_REPO_ROOT = Path("..").resolve()
_ALL_PATCHNOTES = [
    (path, label)
    for path, label in (
        (Path("PATCHNOTES.md"), "LeetKit Manager"),
        (_REPO_ROOT / "mcp" / "PATCHNOTES.md", "StockLens"),
        (_REPO_ROOT / "mcp-dart" / "PATCHNOTES.md", "DartLens"),
        (_REPO_ROOT / "telegramlens" / "PATCHNOTES.md", "TelegramLens"),
    )
    if path.is_file()
]

SAMPLE = """# StockLens 패치노트

<!--
작성 규칙은 화면에 나가면 안 된다.
## 9.9.9 — 2020-01-01
- 주석 안의 예시
-->

소개 문단은 버린다.

## 0.5.11 — 2026-08-08

- 첫 번째 항목
- 두 번째 항목

> 올릴 때 주의할 점.

## 0.5.10 - 2026-08-05
* 붙임표와 별표로 써도 읽힌다
"""


class TestParse:
    def test_reads_versions_newest_first(self):
        entries = patch_notes.parse(SAMPLE)
        assert [e.version for e in entries] == ["0.5.11", "0.5.10"]
        assert [e.date for e in entries] == ["2026-08-08", "2026-08-05"]

    def test_reads_items(self):
        assert patch_notes.parse(SAMPLE)[0].items == ["첫 번째 항목", "두 번째 항목"]

    def test_quote_becomes_note_not_an_item(self):
        """주의사항이 목록에 섞이면 그냥 지나쳐 읽힌다 — 따로 담아 화면에서 떼어놓는다."""
        first = patch_notes.parse(SAMPLE)[0]
        assert first.note == "올릴 때 주의할 점."
        assert "올릴 때 주의할 점." not in first.items

    def test_comment_block_is_not_shown(self):
        """작성 규칙(HTML 주석)이 고객 화면에 나가면 안 된다."""
        assert "9.9.9" not in [e.version for e in patch_notes.parse(SAMPLE)]
        assert all("주석 안의 예시" not in i for e in patch_notes.parse(SAMPLE) for i in e.items)

    def test_accepts_plain_hyphen_and_asterisk(self):
        """사람이 쓰는 파일이라 대시 하나 때문에 항목이 사라지면 안 된다."""
        second = patch_notes.parse(SAMPLE)[1]
        assert second.version == "0.5.10"
        assert second.items == ["붙임표와 별표로 써도 읽힌다"]

    def test_intro_before_first_heading_is_dropped(self):
        assert all("소개 문단" not in i for e in patch_notes.parse(SAMPLE) for i in e.items)

    def test_limit_keeps_the_newest(self):
        assert [e.version for e in patch_notes.parse(SAMPLE, limit=1)] == ["0.5.11"]

    def test_garbage_does_not_raise(self):
        """파일이 깨져도 앱이 죽으면 안 된다 — 읽히는 만큼만."""
        assert patch_notes.parse("") == []
        assert patch_notes.parse("아무 형식도 아닌 글") == []


class TestFetch:
    def test_network_failure_is_empty_not_an_error(self):
        with patch("httpx.get", side_effect=OSError("offline")):
            assert patch_notes.fetch_one("stocklens-mcp") == []

    def test_one_broken_repo_does_not_hide_the_others(self):
        """넷이 다 떠야만 화면이 나오면, 하나가 잠깐 안 열릴 때 아무것도 못 본다."""
        def fake_get(url, **kwargs):
            if "stocklens-mcp" in url:
                raise OSError("offline")
            response = type("R", (), {})()
            response.text = "## 1.0.0 — 2026-08-08\n- 살아있는 항목\n"
            response.raise_for_status = lambda: None
            return response

        with patch("httpx.get", side_effect=fake_get):
            products = patch_notes.fetch_all()

        by_name = {p["name"]: p for p in products}
        assert by_name["stocklens"]["entries"] == []
        assert by_name["dartlens"]["entries"][0]["items"] == ["살아있는 항목"]


class TestShippedFiles:
    """리포에 커밋된 파일이 곧 고객이 보는 화면이다 — 깨져 있으면 빈 칸이 나간다."""

    def test_manager_patchnotes_parses_and_matches_version(self):
        from leetkit_manager import __version__

        entries = patch_notes.parse(Path("PATCHNOTES.md").read_text(encoding="utf-8"))
        assert entries, "PATCHNOTES.md가 안 읽힌다"
        assert entries[0].version == __version__, (
            "맨 위 절이 지금 버전이어야 한다 — 릴리스 때 CI(.github/check_patchnotes.py)가 막지만, "
            "여기서 먼저 걸리는 편이 빠르다"
        )
        assert entries[0].items

    def test_no_developer_jargon_leaks_into_customer_text(self):
        """고객은 원인이 아니라 자기가 겪던 증상으로 기억한다.

        여기 걸리는 말들은 앱 화면에 한 번도 안 나오는 것들이다 — 패치노트에서
        처음 보면 "이게 뭐지" 하고 멈춘다. uv가 대표적이다(없으면 앱이 알아서
        깔아주므로 사용자는 그 이름을 볼 일이 없다). 반대로 "MCP 등록"이나
        "DART 인증키"는 화면의 버튼·입력칸에 그대로 있는 말이라 쓰는 게 맞다.
        """
        # 한글 조사가 붙으면 단어 경계가 안 잡혀서 부분 일치로 본다.
        banned = (
            "uv", "PyInstaller", "subprocess", "_MEIPASS", "traceback", "commit",
            "refactor", "PATH", "데몬", "백필", "stepper", "doctor", "CLI", "PID",
            "JSON", "config", "SDK", "venv", "CI",
        )
        for path, label in _ALL_PATCHNOTES:
            entries = patch_notes.parse(path.read_text(encoding="utf-8"))
            text = " ".join([i for e in entries for i in e.items] + [e.note for e in entries])
            for word in banned:
                assert word.lower() not in text.lower(), f"{label}에 개발 용어가 나간다: {word}"

    def test_every_product_has_history_from_the_start(self):
        """이력 관리는 2026-08-06(첫 배포)부터다. 최신 하나만 있으면 "이전에
        무엇이 바뀌었나"를 볼 수 없고, 화면의 이력 기능이 빈 채로 남는다."""
        for path, label in _ALL_PATCHNOTES:
            entries = patch_notes.parse(path.read_text(encoding="utf-8"))
            assert len(entries) >= 2, f"{label}: 이력이 {len(entries)}개뿐"
            dates = [e.date for e in entries]
            assert dates == sorted(dates, reverse=True), f"{label}: 최신순이 아니다 {dates}"
            assert min(dates) >= "2026-08-06", f"{label}: 관리 시작일 이전 기록 {min(dates)}"

    def test_versions_are_unique_and_newest_first(self):
        """버전은 안 겹치고 최신순이어야 한다 — 앱이 이 순서대로 그리고,
        "여기까지 쓰고 계십니다" 경계도 이 순서를 믿는다.

        같은 날짜가 두 번 나오는 건 허용한다. 처음엔 "날짜당 절 하나"로 묶었는데,
        그건 이미 지나간 이력을 정리할 때 맞는 규칙이고 앞으로는 틀린다 — 한 번
        내보낸 버전의 절은 그대로 굳어야 한다. 같은 날 두 번 내면서 하나로 합치면,
        먼저 받은 사람에게 이미 갖고 있는 것까지 "업데이트하면 적용"으로 보여준다.
        """
        for path, label in _ALL_PATCHNOTES:
            entries = patch_notes.parse(path.read_text(encoding="utf-8"))
            versions = [e.version for e in entries]
            assert len(versions) == len(set(versions)), f"{label}: 같은 버전이 두 번 {versions}"
            keys = [tuple(int(n) for n in re.findall(r"\d+", v)) for v in versions]
            assert keys == sorted(keys, reverse=True), f"{label}: 최신순이 아니다 {versions}"

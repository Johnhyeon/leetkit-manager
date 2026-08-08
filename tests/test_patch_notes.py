"""PATCHNOTES.md 파서 — 구매자가 앱에서 그대로 읽는 화면이라, 손으로 쓰는 파일의
사소한 어긋남(대시 종류, 빈 줄, 주석) 때문에 내용이 통째로 사라지면 안 된다."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from leetkit_manager import patch_notes

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
        """고객은 원인이 아니라 자기가 겪던 증상으로 기억한다."""
        entries = patch_notes.parse(Path("PATCHNOTES.md").read_text(encoding="utf-8"))
        text = " ".join(i for e in entries for i in e.items)
        for word in ("PyInstaller", "subprocess", "_MEIPASS", "traceback", "commit", "refactor"):
            assert word.lower() not in text.lower(), f"개발 용어가 고객 화면에 나간다: {word}"

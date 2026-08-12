from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from leetkit_manager import trial_usage


@pytest.fixture
def home(tmp_path, monkeypatch):
    """세 Lens의 기록 위치를 전부 임시 폴더로 돌린다 — 이 PC의 실제 사용 기록이
    결과에 섞이면 테스트가 사람마다 다르게 나온다."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("DARTLENS_HOME", str(tmp_path / ".dartlens"))
    monkeypatch.setenv("TELEGRAMLENS_HOME", str(tmp_path / ".telegramlens"))
    with patch.object(trial_usage.Path, "home", staticmethod(lambda: tmp_path)):
        yield tmp_path


def _write_metrics(folder: Path, day: str, count: int, tool: str = "get_chart") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"metrics_{day}.jsonl").write_text(
        "".join(
            json.dumps({"timestamp": f"2026-08-{day[-2:]}T10:00:00", "tool": tool}) + "\n"
            for _ in range(count)
        ),
        encoding="utf-8",
    )


def _labels(rows: list[dict]) -> dict:
    return {r["label"]: r["value"] for r in rows}


class TestNothingToShow:
    """숫자를 지어내지 않는다. 안 쓴 사람에게 "0회"를 보여주면 '안 썼네'로 읽혀
    오히려 반대로 설득한다 — 그럴 바엔 그 줄을 아예 안 보여준다."""

    def test_no_records_gives_empty(self, home):
        assert trial_usage.summary() == []

    def test_unreadable_folder_is_silent(self, home):
        """macOS는 Downloads가 권한으로 막힐 수 있다 — 못 읽는 것과 안 쓴 것을
        구분할 수 없으니 조용히 뺀다."""
        with patch.object(Path, "glob", side_effect=PermissionError("nope")):
            assert trial_usage.summary() == []

    def test_broken_line_does_not_break_the_count(self, home):
        folder = home / "Downloads" / "kstock" / "logs"
        folder.mkdir(parents=True)
        (folder / "metrics_20260806.jsonl").write_text(
            json.dumps({"timestamp": "2026-08-06T10:00:00", "tool": "get_chart"}) + "\n{잘린",
            encoding="utf-8",
        )
        assert _labels(trial_usage.summary())["Claude에게 물어본 횟수"] == "1회"


class TestCounts:
    def test_calls_are_summed_across_lenses(self, home):
        _write_metrics(home / "Downloads" / "kstock" / "logs", "20260806", 40)
        _write_metrics(home / ".dartlens" / "logs", "20260806", 8, tool="search_company")
        rows = _labels(trial_usage.summary())
        assert rows["Claude에게 물어본 횟수"] == "48회"
        assert rows["StockLens 시세·재무 조회"] == "40회"
        assert rows["DartLens 공시·재무 조회"] == "8회"

    def test_days_used_counts_distinct_dates(self, home):
        """'몇 번'보다 '며칠'이 습관이 됐다는 걸 더 잘 보여준다."""
        for day in ("20260805", "20260806", "20260807"):
            _write_metrics(home / "Downloads" / "kstock" / "logs", day, 5)
        assert _labels(trial_usage.summary())["사용하신 날"] == "3일"

    def test_top_tool_is_translated(self, home):
        _write_metrics(home / "Downloads" / "kstock" / "logs", "20260806", 30, tool="get_chart")
        _write_metrics(home / "Downloads" / "kstock" / "logs", "20260807", 5, tool="get_price")
        assert _labels(trial_usage.summary())["가장 많이 쓰신 기능"] == "차트 조회"

    def test_unknown_tool_name_is_not_shown_raw(self, home):
        """도구 이름을 그대로 보여주면(get_multi_chart_stats) 무슨 말인지 모른다."""
        _write_metrics(home / "Downloads" / "kstock" / "logs", "20260806", 5, tool="get_weird_internal")
        assert "가장 많이 쓰신 기능" not in _labels(trial_usage.summary())

    def test_thousands_are_grouped(self, home):
        _write_metrics(home / "Downloads" / "kstock" / "logs", "20260806", 1234)
        assert _labels(trial_usage.summary())["Claude에게 물어본 횟수"] == "1,234회"


class TestTelegram:
    def _db(self, home, *, messages: int, channels: int, mentions: int) -> None:
        folder = home / ".telegramlens"
        folder.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(folder / "telegramlens.db")
        conn.executescript(
            "CREATE TABLE channels(id INTEGER PRIMARY KEY);"
            "CREATE TABLE messages(id INTEGER PRIMARY KEY, channel_id INTEGER);"
            "CREATE TABLE mentions(id INTEGER PRIMARY KEY, code TEXT);"
        )
        conn.executemany("INSERT INTO channels VALUES(?)", [(i,) for i in range(1, channels + 1)])
        conn.executemany(
            "INSERT INTO messages VALUES(?,?)", [(i, 1) for i in range(1, messages + 1)]
        )
        conn.executemany(
            "INSERT INTO mentions VALUES(?,?)",
            [(i, f"{i % 7:06d}") for i in range(1, mentions + 1)],
        )
        conn.commit()
        conn.close()

    def test_counts_messages_channels_and_stocks(self, home):
        self._db(home, messages=3400, channels=12, mentions=100)
        rows = _labels(trial_usage.summary())
        assert rows["TelegramLens가 모은 메시지"] == "3,400건"
        assert rows["추적한 채널"] == "12개"
        assert rows["언급을 포착한 종목"] == "7개"

    def test_missing_db_is_silent(self, home):
        assert trial_usage.summary() == []

    def test_old_db_without_mentions_still_reports_the_rest(self, home):
        """표 하나가 없다고 전부 못 보여주면 안 된다 — 있는 것만 보여준다."""
        folder = home / ".telegramlens"
        folder.mkdir(parents=True)
        conn = sqlite3.connect(folder / "telegramlens.db")
        conn.executescript(
            "CREATE TABLE channels(id INTEGER PRIMARY KEY);"
            "CREATE TABLE messages(id INTEGER PRIMARY KEY);"
        )
        conn.executemany("INSERT INTO messages VALUES(?)", [(i,) for i in range(1, 6)])
        conn.commit()
        conn.close()
        rows = _labels(trial_usage.summary())
        assert rows["TelegramLens가 모은 메시지"] == "5건"
        assert "언급을 포착한 종목" not in rows

    def test_reads_without_locking_the_daemon_out(self, home):
        """수집 데몬이 쓰는 중일 수 있다 — 읽기 전용으로 열어야 한다."""
        self._db(home, messages=5, channels=1, mentions=1)
        db = home / ".telegramlens" / "telegramlens.db"
        holder = sqlite3.connect(db)
        holder.execute("BEGIN IMMEDIATE")  # 데몬이 쓰기 중인 상황
        try:
            assert _labels(trial_usage.summary())["TelegramLens가 모은 메시지"] == "5건"
        finally:
            holder.rollback()
            holder.close()

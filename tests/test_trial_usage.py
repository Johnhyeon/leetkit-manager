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


class TestOnlyCountsFromActivation:
    """로그는 지워지지 않고 계속 쌓인다. 무료로 써보던 시절 기록까지 같이 세면
    14일 체험이 끝난 화면에 "사용하신 날 80일"이 뜬다 — 본인이 바로 아는 거짓말이라
    같은 화면의 나머지 숫자까지 의심받는다."""

    def _activated(self, day: str):
        """license.key를 그날 넣었다고 치는 패치."""
        import time
        from datetime import datetime

        ts = time.mktime(datetime.fromisoformat(day + "T09:00:00").timetuple())
        return patch("leetkit_manager.review_prompt.license_activated_at", lambda: ts)

    def test_records_before_activation_are_excluded(self, home):
        logs = home / "Downloads" / "kstock" / "logs"
        _write_metrics(logs, "20260801", 500)  # 체험 전 (무료 시절)
        _write_metrics(logs, "20260810", 12)   # 체험 중
        with self._activated("2026-08-05"):
            rows = _labels(trial_usage.summary())
        assert rows["Claude에게 물어본 횟수"] == "12회"
        assert rows["사용하신 날"] == "1일"

    def test_activation_day_itself_counts(self, home):
        """그날 키를 넣고 바로 써본 사람의 기록이 빠지면 안 된다."""
        logs = home / "Downloads" / "kstock" / "logs"
        _write_metrics(logs, "20260805", 7)
        with self._activated("2026-08-05"):
            assert _labels(trial_usage.summary())["Claude에게 물어본 횟수"] == "7회"

    def test_unknown_activation_counts_everything(self, home):
        """활성화 시각을 모르면 창을 지어내지 않는다 — 예전 동작 그대로."""
        logs = home / "Downloads" / "kstock" / "logs"
        _write_metrics(logs, "20260801", 5)
        with patch("leetkit_manager.review_prompt.license_activated_at", lambda: None):
            assert _labels(trial_usage.summary())["Claude에게 물어본 횟수"] == "5회"

    def test_top_tool_follows_the_window_too(self, home):
        """체험 전에 많이 쓴 도구가 '가장 많이 쓰신 기능'으로 뽑히면 안 된다."""
        logs = home / "Downloads" / "kstock" / "logs"
        _write_metrics(logs, "20260801", 100, tool="get_price")
        _write_metrics(logs, "20260810", 3, tool="get_financial")
        with self._activated("2026-08-05"):
            assert _labels(trial_usage.summary())["가장 많이 쓰신 기능"] == "재무 조회"


class TestLogsMovedOutOfDownloads:
    """StockLens 로그는 2026-08 에 ~/Downloads/kstock/logs 에서 ~/.stocklens/logs 로
    옮겼다. 그 전에 설치한 사람 것은 옛 자리에 그대로 있으므로 둘 다 읽어야 한다 —
    한쪽만 보면 오래 쓴 사람 화면에 "0회"가 떠서 안 쓴 사람으로 오해하게 만든다."""

    def _old(self, home: Path) -> Path:
        return home / "Downloads" / "kstock" / "logs"

    def _new(self, home: Path) -> Path:
        return home / ".stocklens" / "logs"

    def test_old_location_alone_still_counts(self, home, monkeypatch):
        monkeypatch.setenv("STOCKLENS_HOME", str(home / ".stocklens"))
        _write_metrics(self._old(home), "20260801", 7)
        with patch("leetkit_manager.review_prompt.license_activated_at", lambda: None):
            assert _labels(trial_usage.summary())["Claude에게 물어본 횟수"] == "7회"

    def test_new_location_alone_counts(self, home, monkeypatch):
        monkeypatch.setenv("STOCKLENS_HOME", str(home / ".stocklens"))
        _write_metrics(self._new(home), "20260801", 4)
        with patch("leetkit_manager.review_prompt.license_activated_at", lambda: None):
            assert _labels(trial_usage.summary())["Claude에게 물어본 횟수"] == "4회"

    def test_both_locations_add_up(self, home, monkeypatch):
        """옮긴 뒤에도 옛 기록이 사라지면 안 된다 — 합쳐서 보여준다."""
        monkeypatch.setenv("STOCKLENS_HOME", str(home / ".stocklens"))
        _write_metrics(self._old(home), "20260801", 7)
        _write_metrics(self._new(home), "20260810", 4)
        with patch("leetkit_manager.review_prompt.license_activated_at", lambda: None):
            rows = _labels(trial_usage.summary())
        assert rows["Claude에게 물어본 횟수"] == "11회"
        assert rows["사용하신 날"] == "2일"

    def test_same_day_in_both_places_is_not_double_counted(self, home, monkeypatch):
        """옮기던 날 하루가 양쪽에 걸칠 수 있다. 두 번 세면 사용량이 부풀려진다."""
        monkeypatch.setenv("STOCKLENS_HOME", str(home / ".stocklens"))
        _write_metrics(self._old(home), "20260805", 9)
        _write_metrics(self._new(home), "20260805", 3)
        with patch("leetkit_manager.review_prompt.license_activated_at", lambda: None):
            rows = _labels(trial_usage.summary())
        assert rows["Claude에게 물어본 횟수"] == "3회"  # 새 위치가 이긴다
        assert rows["사용하신 날"] == "1일"

"""체험 기간에 실제로 쌓인 기록을 센다 — 기간이 끝난 화면에서 보여줄 숫자.

왜 이걸 보여주나. 기간이 끝난 자리에서 "구매하세요"라고 말하는 것보다 **본인이 만든
결과**를 보여주는 쪽이 훨씬 강하다. 남이 쓴 광고 문구는 의심하지만 자기가 남긴
기록은 의심하지 않는다.

지어내지 않는다. 셀 수 없으면 그 줄을 아예 빼고, 0이면 안 보여준다 — "0건"은
"안 썼네"로 읽혀서 오히려 반대로 설득한다. 파일을 못 읽는 것과 진짜로 안 쓴 것을
구분할 수 없으니, 애매하면 말하지 않는 쪽을 고른다(macOS는 Downloads 폴더가 권한으로
막힐 수 있어 실제로 이 경우가 생긴다).

읽는 파일은 전부 각 Lens가 이미 남기고 있는 것들이다 — 이걸 위해 새로 수집하는 건
없다. 숫자는 이 컴퓨터 밖으로 나가지 않는다(화면에만 쓴다).
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

# 도구 이름을 그대로 보여주면(get_multi_chart_stats) 무슨 말인지 모른다. 자주 쓰이는
# 것만 사람 말로 옮기고, 모르는 이름은 아예 그 줄을 안 보여준다.
_TOOL_LABELS = {
    "get_chart": "차트 조회",
    "get_price": "현재가 조회",
    "get_indicators": "기술지표",
    "get_financial": "재무 조회",
    "get_flow": "수급 조회",
    "search": "종목 검색",
    "search_stock": "종목 검색",
    "get_consensus": "컨센서스",
    "get_disclosure": "공시 조회",
    "list_disclosures": "공시 목록",
    "get_major_accounts": "핵심 재무",
    "get_full_financial": "재무제표",
    "search_company": "기업 검색",
    "get_major_holders": "대량보유 변동",
    "get_insider_trades": "내부자 매매",
}


def _metrics_dir(env_var: str, default: Path) -> Path:
    override = os.environ.get(env_var)
    return Path(override) if override else default


def _read_metrics(folder: Path) -> list[dict]:
    """metrics_*.jsonl 전체를 레코드 목록으로. 못 읽으면 빈 목록."""
    try:
        files = sorted(folder.glob("metrics_*.jsonl"))
    except OSError:
        return []  # macOS 권한 등 — 못 읽는 것과 안 쓴 것을 구분할 수 없으니 조용히 뺀다
    records: list[dict] = []
    for f in files:
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except ValueError:
                        continue  # 쓰는 도중에 잘린 마지막 줄 — 버린다
        except OSError:
            continue
    return records


def _stocklens_metrics() -> list[dict]:
    base = Path(os.environ.get("USERPROFILE") or str(Path.home()))
    return _read_metrics(base / "Downloads" / "kstock" / "logs")


def _dartlens_metrics() -> list[dict]:
    home = _metrics_dir("DARTLENS_HOME", Path.home() / ".dartlens")
    return _read_metrics(home / "logs")


def _telegram_counts() -> dict:
    """모은 메시지·채널·종목 언급 수. DB를 못 열면 빈 dict."""
    home = _metrics_dir("TELEGRAMLENS_HOME", Path.home() / ".telegramlens")
    db = home / "telegramlens.db"
    if not db.is_file():
        return {}
    try:
        # 읽기 전용으로 연다 — 수집 데몬이 쓰는 중일 수 있어 잠금을 만들면 안 된다.
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return {}
    out: dict = {}
    try:
        for key, sql in (
            ("messages", "SELECT COUNT(*) FROM messages"),
            ("channels", "SELECT COUNT(*) FROM channels"),
            ("mentions", "SELECT COUNT(*) FROM mentions"),
            ("stocks", "SELECT COUNT(DISTINCT code) FROM mentions"),
        ):
            try:
                out[key] = conn.execute(sql).fetchone()[0] or 0
            except sqlite3.Error:
                pass  # 옛 DB에 없는 표 — 그 줄만 빠진다
    finally:
        conn.close()
    return out


def _days_used(records: list[dict]) -> int:
    """며칠에 걸쳐 썼는지. '몇 번'보다 '며칠'이 습관이 됐다는 걸 더 잘 보여준다."""
    days = {r.get("timestamp", "")[:10] for r in records if r.get("timestamp")}
    days.discard("")
    return len(days)


def _top_tool(records: list[dict]) -> str | None:
    """가장 많이 쓴 도구를 사람 말로. 이름을 모르면 None."""
    counts = Counter(r.get("tool") for r in records if r.get("tool"))
    for name, _ in counts.most_common():
        label = _TOOL_LABELS.get(name)
        if label:
            return label
    return None


def summary() -> list[dict]:
    """[{label, value}] — 보여줄 게 없으면 빈 목록.

    값은 화면에 그대로 나가므로 여기서 사람이 읽는 형태로 만든다.
    """
    rows: list[dict] = []

    stock = _stocklens_metrics()
    dart = _dartlens_metrics()
    calls = len(stock) + len(dart)
    if calls:
        rows.append({"label": "Claude에게 물어본 횟수", "value": f"{calls:,}회"})

    days = _days_used(stock + dart)
    if days:
        rows.append({"label": "사용하신 날", "value": f"{days:,}일"})

    if stock:
        rows.append({"label": "StockLens 시세·재무 조회", "value": f"{len(stock):,}회"})
    if dart:
        rows.append({"label": "DartLens 공시·재무 조회", "value": f"{len(dart):,}회"})

    top = _top_tool(stock + dart)
    if top:
        rows.append({"label": "가장 많이 쓰신 기능", "value": top})

    tg = _telegram_counts()
    if tg.get("messages"):
        rows.append({"label": "TelegramLens가 모은 메시지", "value": f"{tg['messages']:,}건"})
    if tg.get("channels"):
        rows.append({"label": "추적한 채널", "value": f"{tg['channels']:,}개"})
    if tg.get("stocks"):
        rows.append({"label": "언급을 포착한 종목", "value": f"{tg['stocks']:,}개"})

    return rows

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

**라이선스를 넣은 날부터만 센다.** 로그 파일은 지우지 않고 계속 쌓이므로, 그냥 다
세면 무료로 써보던 시절 기록까지 들어간다. 그러면 14일 체험이 끝난 화면에 "사용하신
날 80일"이 뜬다 — 본인이 바로 아는 거짓말이고, 여기서 신뢰를 잃으면 같은 화면의
나머지 숫자도 같이 의심받는다. 우리가 파는 게 "믿을 수 있는 데이터"라 더 그렇다.
활성화 시각을 모르면(라이선스 파일을 못 읽는 등) 자르지 않고 전부 센다 — 창을
지어내느니 예전 동작이 낫다.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
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


def _read_metrics_files(files: "list[Path]") -> list[dict]:
    """주어진 jsonl 파일들을 레코드 목록으로. 못 읽는 파일은 건너뛴다."""
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


def _read_metrics(*folders: Path) -> list[dict]:
    """여러 폴더의 metrics_*.jsonl 을 합쳐 읽는다. 못 읽으면 그 폴더만 빠진다.

    같은 파일명이 두 폴더에 있으면 앞 폴더 것만 쓴다 — 로그 위치를 옮기던 날 하루가
    양쪽에 걸칠 수 있는데, 둘 다 세면 그날 사용량이 두 배로 보인다.
    """
    picked: dict[str, Path] = {}
    for folder in folders:
        try:
            for f in folder.glob("metrics_*.jsonl"):
                picked.setdefault(f.name, f)
        except OSError:
            continue  # macOS 권한 등 — 못 읽는 것과 안 쓴 것을 구분할 수 없으니 조용히 뺀다
    return _read_metrics_files([picked[name] for name in sorted(picked)])


def _stocklens_metrics() -> list[dict]:
    """새 위치와 옛 위치를 합쳐 읽는다.

    2026-08 이전 설치자는 로그가 ~/Downloads/kstock/logs 에 있다. 한쪽만 보면 오래
    쓴 사람의 화면에 "0회"가 떠서, 안 쓴 사람으로 오해하게 만든다.
    """
    home = _metrics_dir("STOCKLENS_HOME", Path.home() / ".stocklens")
    base = Path(os.environ.get("USERPROFILE") or str(Path.home()))
    return _read_metrics(home / "logs", base / "Downloads" / "kstock" / "logs")


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


def _activated_on() -> str | None:
    """라이선스를 처음 넣은 날(YYYY-MM-DD). 모르면 None.

    후기 요청이 쓰는 것과 같은 기준(license.key 파일의 수정 시각)이다. 체험 키를
    붙여넣은 순간이라 체험 시작일과 사실상 같다."""
    try:
        from leetkit_manager.review_prompt import license_activated_at

        ts = license_activated_at()
    except Exception:
        return None  # 후기 모듈이 없거나 읽기 실패 — 자르지 않는다
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _since(records: list[dict], day: str | None) -> list[dict]:
    """day(YYYY-MM-DD) 이후 기록만. day가 없으면 그대로 돌려준다.

    metrics의 timestamp는 로컬시각 ISO('2026-08-16T10:40:53')라 앞 10자가 날짜다."""
    if not day:
        return records
    return [r for r in records if (r.get("timestamp") or "")[:10] >= day]


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
    """[{key, label, value}] — 보여줄 게 없으면 빈 목록. key는 화면이 특정 항목을
    집어 쓰려고 붙인다(예: 텔레그램 수집량은 풀 패키지 안내에 다시 인용된다).

    값은 화면에 그대로 나가므로 여기서 사람이 읽는 형태로 만든다.
    """
    rows: list[dict] = []

    start = _activated_on()
    stock = _since(_stocklens_metrics(), start)
    dart = _since(_dartlens_metrics(), start)
    calls = len(stock) + len(dart)
    if calls:
        rows.append({"key": "calls", "label": "Claude에게 물어본 횟수", "value": f"{calls:,}회"})

    days = _days_used(stock + dart)
    if days:
        rows.append({"key": "days", "label": "사용하신 날", "value": f"{days:,}일"})

    if stock:
        rows.append({"key": "stocklens", "label": "StockLens 시세·재무 조회", "value": f"{len(stock):,}회"})
    if dart:
        rows.append({"key": "dartlens", "label": "DartLens 공시·재무 조회", "value": f"{len(dart):,}회"})

    top = _top_tool(stock + dart)
    if top:
        rows.append({"key": "top_tool", "label": "가장 많이 쓰신 기능", "value": top})

    tg = _telegram_counts()
    if tg.get("messages"):
        rows.append({"key": "telegram_messages", "label": "TelegramLens가 모은 메시지", "value": f"{tg['messages']:,}건"})
    if tg.get("channels"):
        rows.append({"key": "telegram_channels", "label": "추적한 채널", "value": f"{tg['channels']:,}개"})
    if tg.get("stocks"):
        rows.append({"key": "telegram_stocks", "label": "언급을 포착한 종목", "value": f"{tg['stocks']:,}개"})

    return rows

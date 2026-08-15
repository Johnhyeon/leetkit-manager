"""패치노트 — 각 리포의 PATCHNOTES.md를 그대로 읽어 앱 안에서 보여준다.

**왜 앱 안인가.** 예전엔 [패치노트] 버튼이 브라우저로 Notion 페이지 하나를 열었다.
그 방식은 두 가지를 못 한다.

1. 그 사람이 무엇을 샀는지 모른다. STOCK 단품 구매자에게 TelegramLens 변경사항을 보여줄
   이유가 없고, 보이면 "내가 못 받는 게 있나" 싶어진다.
2. 지금 깔린 버전을 모른다. "이건 이미 쓰고 계신 버전에 들어 있고, 이건 업데이트하면
   적용됩니다"를 구분해줄 수 없다.
그리고 주 고객층(40-50대)에게 외부 링크는 로그인 요구·앱 설치 유도가 뜰 수 있는데,
그 순간 "뭔가 잘못됐나" 하고 멈춘다. 앱 안에서 끝나는 편이 안전하다.

**왜 CI가 모아주지 않고 앱이 직접 읽나.** Lens는 각자 따로 릴리스된다. 매니저가
빌드할 때 모아두면, StockLens만 새로 나온 경우 매니저를 다시 릴리스하기 전까지
그 내용이 안 보인다. 각 리포의 main을 직접 읽으면 그런 어긋남이 아예 없고,
문구 오타를 고칠 때도 커밋만 하면 된다(review_prompt.json과 같은 방식).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

_TIMEOUT = 6.0
_RAW = "https://raw.githubusercontent.com/Johnhyeon/{repo}/main/PATCHNOTES.md"

# lens 이름(진단 결과의 name과 같은 값) → 리포. 매니저 자신도 같은 방식으로 읽는다.
SOURCES: tuple[tuple[str, str, str], ...] = (
    ("leetkit-manager", "LeetKit Manager", "leetkit-manager"),
    ("stocklens", "StockLens", "stocklens-mcp"),
    ("dartlens", "DartLens", "dartlens-mcp"),
    ("telegramlens", "TelegramLens", "telegramlens-mcp"),
)

# `## 0.5.11 — 2026-08-08` — em dash(—)를 쓰되 붙임표(-)로 적어도 읽어준다.
# 사람이 손으로 쓰는 파일이라, 대시 하나 때문에 항목이 통째로 사라지면 안 된다.
_HEADING = re.compile(r"^##\s+v?(?P<version>[0-9][0-9A-Za-z.\-+]*)\s*[—–-]\s*(?P<date>\S+)\s*$")
_BULLET = re.compile(r"^[-*]\s+(?P<text>.+?)\s*$")
_QUOTE = re.compile(r"^>\s*(?P<text>.+?)\s*$")


@dataclass
class Entry:
    version: str
    date: str
    items: list[str] = field(default_factory=list)
    # 인용문(`> …`) — "이 버전으로 올릴 땐 이런 일이 있을 수 있습니다" 같은 주의.
    # 목록과 섞으면 그냥 지나쳐 읽히므로 따로 담는다.
    note: str = ""

    def as_dict(self) -> dict:
        return {"version": self.version, "date": self.date, "items": list(self.items), "note": self.note}


def parse(markdown: str, *, limit: int = 10) -> list[Entry]:
    """PATCHNOTES.md 본문 → 버전별 항목. 형식이 어긋난 줄은 조용히 건너뛴다.

    파일이 깨졌다고 앱이 죽거나 빈 화면을 보여주면 안 된다 — 읽히는 만큼만 보여준다.
    HTML 주석(작성 규칙)은 화면에 나가면 안 되므로 먼저 걷어낸다.
    """
    body = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
    entries: list[Entry] = []
    current: Entry | None = None
    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading:
            if len(entries) >= limit:
                break
            current = Entry(version=heading["version"], date=heading["date"])
            entries.append(current)
            continue
        if current is None:
            continue  # 첫 버전 제목 앞의 소개 문단은 버린다
        bullet = _BULLET.match(line)
        if bullet:
            current.items.append(bullet["text"])
            continue
        quote = _QUOTE.match(line)
        if quote:
            current.note = f"{current.note}\n{quote['text']}".strip() if current.note else quote["text"]
    return [e for e in entries if e.items or e.note]


def fetch_one(repo: str, *, timeout: float = _TIMEOUT) -> list[Entry]:
    """한 리포의 패치노트. 못 받으면 빈 목록 — 나머지는 그대로 보여준다.

    넷이 다 떠야만 화면이 나오면, 리포 하나가 잠깐 안 열릴 때 아무것도 못 보게 된다.
    """
    try:
        response = httpx.get(_RAW.format(repo=repo), timeout=timeout, follow_redirects=True)
        response.raise_for_status()
    except Exception:
        return []
    return parse(response.text)


def fetch_all(*, timeout: float = _TIMEOUT) -> list[dict]:
    """네 제품 전부. 화면 순서 그대로(매니저 먼저, 그다음 Lens 셋)."""
    result = []
    for name, display_name, repo in SOURCES:
        entries = fetch_one(repo, timeout=timeout)
        result.append(
            {
                "name": name,
                "display_name": display_name,
                "entries": [e.as_dict() for e in entries],
            }
        )
    return result

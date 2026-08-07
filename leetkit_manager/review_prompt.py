"""후기 요청 모달 — "언제 물어볼지"와 "어디로 보낼지"를 결정한다.

**링크를 코드에 박지 않는 이유.** 후기를 받는 곳은 바뀐다(리틀리 후기 → 구글 폼 →
다른 곳). 후기가 충분히 쌓여서 그만 물어보고 싶어질 수도 있다. URL을 exe에 박아두면
그때마다 릴리스를 새로 내야 하고, 업데이트하지 않은 사람에게는 영영 옛 링크가 남는다.
그래서 링크·문구·on/off를 전부 원격 JSON 하나에서 읽는다 — 그 파일만 고치면 이미
설치된 사람에게도 다음 실행부터 반영된다.

**기본값은 "안 띄움"이다.** 원격 설정을 못 받았거나(오프라인·파일 없음), `enabled`가
false거나, URL이 비어 있으면 조용히 아무것도 안 한다. 후기 요청은 없어도 제품이
동작하는 기능이라, 실패했을 때 사용자에게 보일 이유가 없다.

**언제 물어보나.** 첫 실행에는 절대 안 띄운다 — 아직 써보지도 않은 사람에게 후기를
달라는 건 무례하고, 받아봐야 내용도 없다. 설치를 마치고(정상 Lens가 하나 이상),
며칠 쓰고, 몇 번 열어본 뒤에 한 번 묻는다. "나중에"를 고르면 한참 뒤에 다시 묻되
정해진 횟수까지만, "이미 남겼어요"나 실제로 링크를 열면 다시는 안 묻는다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

# 이 리포의 파일을 그대로 읽는다 — 새 인프라 없이 GitHub만으로 끝난다(자동 업데이트도
# 이미 GitHub를 본다). main에 커밋하는 순간 반영되므로 릴리스가 필요 없다.
CONFIG_URL = (
    "https://raw.githubusercontent.com/Johnhyeon/leetkit-manager/main/review_prompt.json"
)
_CONFIG_TIMEOUT = 5.0

_DEFAULTS = {
    "enabled": False,
    "url": "",
    "title": "후기 한 줄 부탁드립니다",
    # 줄바꿈 문자를 그대로 살려 문단을 나눈다(style.css의 .review-body가
    # white-space: pre-line). 원격 설정에서 문구를 고칠 때 줄 나누기까지 같이
    # 조절할 수 있어야 한다 — 한 덩어리 줄글은 읽다가 그냥 닫힌다.
    "body": (
        "구매하실 때 받으신 메일에서 남기실 수 있습니다.\n\n"
        "제목: [리틀리] 디지털 파일이 도착했습니다💌\n\n"
        "메일 안의 \"파일보기\"를 누르면\n"
        "페이지 맨 아래에 후기 쓰는 곳이 있습니다.\n\n"
        "좋았던 점이든 아쉬웠던 점이든 괜찮습니다.\n"
        "읽고 다음 버전에 반영합니다."
    ),
    "cta": "후기 남기기",
    "min_days": 7,       # 첫 실행 후 이만큼 지나야 처음 묻는다
    "min_launches": 3,   # 이만큼은 열어봐야 "써봤다"고 볼 수 있다
    "snooze_days": 14,   # "나중에" 이후 다시 묻기까지
    "max_asks": 3,       # 이 횟수를 넘기면 영영 안 묻는다
}

_DAY = 86400.0


def _state_path() -> Path:
    d = Path.home() / ".leetkit-manager"
    d.mkdir(parents=True, exist_ok=True)
    return d / "review_prompt.json"


def _load_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict) -> None:
    try:
        _state_path().write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass  # 후기 요청 하나 때문에 앱이 죽으면 안 된다


def record_launch() -> None:
    """실행 횟수와 최초 실행 시각을 남긴다. 창을 띄우기 전에 한 번만 부른다.

    localStorage가 아니라 파일에 적는 이유: webview 저장소는 프로필이 초기화되면
    같이 날아가는데, 그러면 이미 후기를 남긴 사람에게 처음부터 다시 묻게 된다."""
    state = _load_state()
    state.setdefault("first_launch_at", time.time())
    state["launches"] = int(state.get("launches", 0)) + 1
    _save_state(state)


def fetch_config() -> dict:
    """원격 설정을 읽어 기본값 위에 덮어쓴다. 실패하면 기본값(=안 띄움)."""
    config = dict(_DEFAULTS)
    try:
        response = httpx.get(CONFIG_URL, timeout=_CONFIG_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        remote = response.json()
    except Exception:
        return config  # 오프라인·파일 없음·JSON 깨짐 — 전부 조용히 안 띄움
    if not isinstance(remote, dict):
        return config
    for key in _DEFAULTS:
        if key in remote:
            config[key] = remote[key]
    return config


def _usable_url(config: dict) -> str:
    """https가 아니면 안 쓴다. 원격 파일이 이 앱에서 브라우저를 여는 통로이므로,
    엉뚱한 스킴(file:, javascript: 등)이 흘러들어오지 않게 최소한만 막는다."""
    url = config.get("url")
    if isinstance(url, str) and url.startswith("https://"):
        return url
    return ""


def _int(config: dict, key: str) -> int:
    try:
        return int(config[key])
    except (KeyError, TypeError, ValueError):
        return int(_DEFAULTS[key])


def pending_prompt(config: dict, *, ready: bool, now: float | None = None) -> dict | None:
    """지금 후기를 물어볼 때인지. 아니면 None.

    `ready`는 "설치가 실제로 끝났는가"(정상 Lens가 하나 이상). 아직 설치 중이거나
    문제를 고치는 중인 사람에게 후기를 달라고 하면 역효과다.

    URL은 있어도 되고 없어도 된다. 리틀리 후기란은 구매자마다 주소가 다르고 그 주소는
    구매 확인 메일에만 있어서, 앱이 링크로 보낼 방법이 없다 — 그 경우엔 "메일 어디를
    누르세요"라고 안내만 하고 버튼은 안 만든다. on/off는 `enabled` 하나로만 판단한다."""
    if not config.get("enabled") or not ready:
        return None

    state = _load_state()
    if state.get("done"):
        return None

    asks = int(state.get("asks", 0))
    if asks >= _int(config, "max_asks"):
        return None
    if int(state.get("launches", 0)) < _int(config, "min_launches"):
        return None

    now = time.time() if now is None else now
    first = state.get("first_launch_at")
    if not isinstance(first, (int, float)) or now - first < _int(config, "min_days") * _DAY:
        return None

    last_ask = state.get("last_ask_at")
    if isinstance(last_ask, (int, float)) and now - last_ask < _int(config, "snooze_days") * _DAY:
        return None

    return {
        "title": str(config.get("title") or _DEFAULTS["title"]),
        "body": str(config.get("body") or _DEFAULTS["body"]),
        "cta": str(config.get("cta") or _DEFAULTS["cta"]),
        "url": _usable_url(config),
    }


def mark_asked(now: float | None = None) -> None:
    state = _load_state()
    state["asks"] = int(state.get("asks", 0)) + 1
    state["last_ask_at"] = time.time() if now is None else now
    _save_state(state)


def mark_done() -> None:
    """다시는 묻지 않는다 — 링크를 실제로 열었거나 "이미 남겼어요"를 골랐을 때."""
    state = _load_state()
    state["done"] = True
    _save_state(state)

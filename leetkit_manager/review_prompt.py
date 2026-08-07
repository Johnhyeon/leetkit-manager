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
import os
import time
from pathlib import Path

import httpx

# 각 Lens가 라이선스 키를 저장하는 곳(각 licensing.py의 _home + "license.key").
# 환경변수 우회까지 그대로 따라간다 — 안 그러면 옮겨 쓴 사람을 미구매자로 본다.
_LICENSE_FILES = (
    ("STOCKLENS_HOME", ".stocklens"),
    ("DARTLENS_HOME", ".dartlens"),
    ("TELEGRAMLENS_HOME", ".telegramlens"),
)

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
    # 리틀리 후기는 아무 때나 못 남긴다 — 파일 받는 기간(구매 후 약 한 달)이 만료되면
    # 후기란 자체가 사라진다. 그래서 모든 요청이 그 안에 들어가야 하고, 지나면 물어보는
    # 것 자체가 누를 데 없는 안내가 된다. 아래 값은 3·10·17일째에 묻고 21일에 멈춘다.
    #
    # Manager는 매일 여는 앱이 아니라 설치·진단 도구다. 문턱을 높게 잡으면 조건을
    # 만족하기 전에 기한이 먼저 끝난다 — 그래서 예전 값(7일·3회·14일 간격)보다 낮췄다.
    "min_days": 3,        # 첫 실행 후 이만큼 지나야 처음 묻는다
    "min_launches": 2,    # 설치하느라 연 첫 실행 말고 한 번은 더 열어봤어야 한다
    "snooze_days": 7,     # "나중에" 이후 다시 묻기까지
    "max_asks": 3,        # 이 횟수를 넘기면 영영 안 묻는다
    "deadline_days": 21,  # 첫 실행 후 이만큼 지나면 아예 안 묻는다(후기 기간 만료)

    # 화면에 "앞으로 며칠 남았다"를 보여줄 때 쓰는 실제 규칙(리틀리 기준 약 한 달).
    # 위 deadline_days와 일부러 다르다 — deadline_days는 "언제까지 물어볼까"라서
    # 대용 기준(첫 실행일)의 오차를 흡수하려고 짧게 잡았고, 이 값은 "사용자에게 알려줄
    # 실제 기한"이다. 0으로 두면 남은 기간을 아예 안 보여준다.
    "review_window_days": 30,
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


def license_activated_at() -> float | None:
    """라이선스를 활성화한 시각(가장 이른 것). 활성화한 적이 없으면 None.

    후기 요청의 기준 시각이자 "구매자인가" 판정을 겸한다. 라이선스 파일이 하나도
    없으면 산 적이 없는 사람이므로, 후기를 물어볼 대상이 아니다.

    **왜 첫 실행 시각이 아니라 이걸 쓰나.** Manager는 Lens보다 늦게 나왔다. 두 달 전에
    산 사람이 오늘 Manager를 처음 켜면 첫 실행 시각은 오늘이고, 그걸 기준으로 세면
    이미 닫힌 후기 창을 "30일 남았다"고 알려주게 된다. 활성화 시각은 구매 직후라
    실제 구매일에 훨씬 가깝고, 덕분에 기간이 지난 사람에게는 아예 안 묻게 된다.

    한계: 다른 PC에 새로 깔거나 키를 다시 넣으면 시각이 갱신돼 최근 구매자처럼 보인다.
    그때는 기간이 지났는데도 한 번 물어볼 수 있는데, 화면에 "구매 후 약 30일"이라는
    실제 규칙을 같이 적어두므로 본인이 판단할 수 있다.
    """
    times = []
    for env_var, folder in _LICENSE_FILES:
        base = os.environ.get(env_var)
        path = (Path(base) if base else Path.home() / folder) / "license.key"
        try:
            if path.is_file():
                times.append(path.stat().st_mtime)
        except OSError:
            continue  # 권한 문제 등 — 못 읽으면 없는 셈 친다
    return min(times) if times else None


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

    # 라이선스를 활성화한 적이 없으면 산 적이 없는 사람이다 — 후기를 물어볼 대상이
    # 아니고, 애초에 리틀리 후기란은 구매자에게만 열린다.
    activated_at = license_activated_at()
    if activated_at is None:
        return None

    now = time.time() if now is None else now
    if now - activated_at < _int(config, "min_days") * _DAY:
        return None

    # 후기 기간이 끝났으면 묻지 않는다. 리틀리 후기란은 파일 받는 기간이 만료되면
    # 같이 사라져서, 지난 뒤의 요청은 누를 데 없는 안내가 된다.
    #
    # 활성화 시각은 구매 직후라 구매일에 가깝지만 같지는 않다(메일을 며칠 뒤에 열어
    # 활성화하는 사람이 있다). 그만큼 실제 기한은 이미 줄어든 상태이므로, 마감선을
    # 한 달이 아니라 그보다 짧게 잡아 여유를 둔다.
    if now - activated_at > _int(config, "deadline_days") * _DAY:
        return None

    last_ask = state.get("last_ask_at")
    if isinstance(last_ask, (int, float)) and now - last_ask < _int(config, "snooze_days") * _DAY:
        return None

    return {
        "title": str(config.get("title") or _DEFAULTS["title"]),
        "body": str(config.get("body") or _DEFAULTS["body"]),
        "cta": str(config.get("cta") or _DEFAULTS["cta"]),
        "url": _usable_url(config),
        "deadline_note": _deadline_note(config, activated_at=activated_at, now=now),
    }


def _deadline_note(config: dict, *, activated_at: float, now: float) -> str:
    """"앞으로 며칠 남았다"는 안내 문구. 안 보여줄 상황이면 빈 문자열.

    실제 규칙(구매 후 약 한 달)과 우리가 센 기준(라이선스를 넣은 날)을 **둘 다**
    밝힌다. 앱은 구매 시각을 알 수 없어 활성화 시각으로 대신 세는데, 사서 며칠 뒤에
    활성화한 사람에게 그냥 "N일 남음"이라고 하면 실제보다 넉넉하게 말하는 게 된다 —
    그 사람은 믿고 미루다 기간을 놓친다. 기준을 같이 적어두면 스스로 보정할 수 있다.
    """
    window = _int(config, "review_window_days")
    if window <= 0:
        return ""
    left = int((activated_at + window * _DAY - now) // _DAY)
    if left <= 0:
        return ""
    # **…**로 감싼 부분은 화면에서 굵게 나온다(app.js의 renderEmphasis). 여기서 눈에
    # 걸려야 하는 건 숫자 두 개다 — 나머지 문장은 읽어도 되고 넘겨도 되는 설명이다.
    return (
        f"후기는 구매 후 약 **{window}일**까지만 남길 수 있습니다.\n"
        f"라이선스를 넣으신 날부터 세면 앞으로 **{left}일** 남았습니다."
    )


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

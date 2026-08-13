"""민감정보 마스킹 — "진단 결과 복사" 등 사람이 텍스트를 그대로 밖으로 들고 나갈 때 쓴다.

1차 방어선은 이미 각 Lens의 doctor/activate JSON 자체다(원문 키를 애초에 출력하지 않음).
이 모듈은 2차 방어선이다 — 크래시 stderr, 구버전 Lens의 계약 위반, 사람용 텍스트 출력에
어쩌다 원문이 섞여 나온 경우까지 대비한다. 그래서 규칙은 일부러 넓게(false positive를
감수하고) 잡는다 — 놓치는 것보다 과잉 마스킹이 안전하다.

가리는 대상 (수용 기준 원문):
- 알려진 특정 비밀 값(호출자가 쥐고 있는 경우) — `redact(text, secrets=[...])`
- 라이선스 키 모양(base32, 긴 토큰) / DART API 키·Telegram API Hash 모양(hex, 긴 토큰)
- 전화번호(한국 휴대폰 형식 위주)
- 홈 디렉터리 경로의 사용자명(Windows `C:\\Users\\<name>\\...`, Unix `/home/<name>/...`)
"""

from __future__ import annotations

import re

# 24자였다. 실사용에서 확인된 문제(2026-08-13): Claude Desktop 로그는 긴 URL을 줄바꿈으로
# 접는데, 그러면 40자 DART 키가 `crtfc_key=3c209ef8fb073d\n...aadf3bcee13811c7541af6cebf`
# 처럼 14자+26자로 갈라진다. 24자 규칙은 뒤 조각만 잡고 앞 조각을 흘렸다. 잘린 조각까지
# 걸리도록 문턱을 낮춘다 — 여기서 늘어나는 오탐(커밋 해시 등)은 진단서에서 아무것도
# 잃지 않는다.
_HEX_TOKEN_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_BASE32_TOKEN_RE = re.compile(r"\b[A-Z2-7]{40,}\b")

# 값 모양이 아니라 파라미터 이름으로 지운다 — 위처럼 값이 쪼개지면 길이 규칙은 언제든
# 다시 뚫린다. DART는 crtfc_key를 쿼리스트링으로 받으므로 URL이 통째로 로그에 남으면
# 키도 같이 남는다(확인된 유출 경로).
_QUERY_SECRET_RE = re.compile(
    r"((?:crtfc_key|api_key|apikey|api_hash|access_token|token)\s*=\s*)([0-9A-Za-z][0-9A-Za-z\-_]{3,})",
    re.IGNORECASE,
)
# 점 구분(010.1234.5678)까지 포함 — 실제 로그에서 관찰되는 표기 변형.
_PHONE_RE = re.compile(r"(\+?82[-.\s]?1[0-9]|01[0-9])[-.\s]?\d{3,4}[-.\s]?\d{4}\b")
# 드라이브 문자는 C에 한정하지 않고(D:\Users\... 등), 구분자는 역슬래시·슬래시 둘 다
# 받는다(로그·JSON에는 C:/Users/... 형태가 흔하다). 경로 끝(구분자 없이 끝나는 경우)도
# 잡히도록 마지막 구분자를 선택적으로 둔다 — 예전 규칙은 이 셋 다 놓쳤다.
#
# 구분자를 개수 제한 없이 받는 이유: 로그에 JSON이 실리면 경로가 `C:\\Users\\name` 으로,
# JSON 안에 또 JSON이 실리면 `C:\\\\Users\\\\name` 으로 남는다. 실사용 로그(mcp.log)에서
# 사용자명이 13,957번 등장했는데 예전 규칙은 그중 178건(단일 역슬래시)만 잡았다.
# 이스케이프 깊이를 세는 대신 구분자를 통째로 넘긴다.
_WIN_USER_RE = re.compile(r"([A-Za-z]:[\\/]+Users[\\/]+)([^\\/\s]+)")
# macOS(`/Users/<name>`)도 지원 대상이다(support_bundle이 darwin 경로를 수집한다).
_NIX_USER_RE = re.compile(r"((?:/home|/Users)/)([^/\s]+)")


def _mask_span(s: str, keep: int = 4) -> str:
    if len(s) <= keep:
        return "*" * len(s)
    return "*" * (len(s) - keep) + s[-keep:]


def redact_known_secret(text: str, secret: str | None) -> str:
    """호출자가 쥐고 있는 특정 비밀 값(예: 방금 입력받은 라이선스 키)을 텍스트에서 제거한다."""
    if not secret or not secret.strip():
        return text
    return text.replace(secret, "[REDACTED]")


def redact_key_like_tokens(text: str) -> str:
    """라이선스 키/DART API 키/Telegram API Hash처럼 생긴 긴 hex·base32 토큰을 마스킹."""
    text = _HEX_TOKEN_RE.sub(lambda m: _mask_span(m.group(0)), text)
    text = _BASE32_TOKEN_RE.sub(lambda m: _mask_span(m.group(0)), text)
    return text


def redact_query_secrets(text: str) -> str:
    """`crtfc_key=...` 처럼 이름으로 비밀임이 드러나는 쿼리 파라미터 값을 지운다.

    길이 규칙(redact_key_like_tokens)보다 먼저 돌아야 한다 — 값이 줄바꿈으로 쪼개져
    길이 규칙을 빠져나가는 경우가 이 함수의 존재 이유다.

    지우는 방식은 모듈 관례대로 _mask_span(끝 4자만 남김) — 지원 문의에서 "어느 키인지"는
    구분할 수 있어야 한다. 쪼개진 조각에 걸리면 남는 4자는 키 중간 토막이라 그것만으로
    복원되지 않는다.
    """
    return _QUERY_SECRET_RE.sub(lambda m: f"{m.group(1)}{_mask_span(m.group(2))}", text)


def redact_phone_numbers(text: str) -> str:
    return _PHONE_RE.sub("[PHONE]", text)


def redact_home_username(text: str) -> str:
    text = _WIN_USER_RE.sub(lambda m: f"{m.group(1)}<user>", text)
    text = _NIX_USER_RE.sub(lambda m: f"{m.group(1)}<user>", text)
    return text


def redact(text: str, *, secrets: list[str] | None = None) -> str:
    """전체 마스킹 파이프라인. `secrets`는 호출자가 이번 세션에서 쥐고 있던 원문 값들."""
    if not text:
        return text
    for secret in secrets or []:
        text = redact_known_secret(text, secret)
    text = redact_query_secrets(text)
    text = redact_key_like_tokens(text)
    text = redact_phone_numbers(text)
    text = redact_home_username(text)
    return text

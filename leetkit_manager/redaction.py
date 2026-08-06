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

_HEX_TOKEN_RE = re.compile(r"\b[0-9a-fA-F]{24,}\b")
_BASE32_TOKEN_RE = re.compile(r"\b[A-Z2-7]{40,}\b")
_PHONE_RE = re.compile(r"(\+?82[-\s]?1[0-9]|01[0-9])[-\s]?\d{3,4}[-\s]?\d{4}\b")
_WIN_USER_RE = re.compile(r"(C:\\Users\\)([^\\]+)(\\)", re.IGNORECASE)
_NIX_USER_RE = re.compile(r"(/home/)([^/]+)(/)")


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


def redact_phone_numbers(text: str) -> str:
    return _PHONE_RE.sub("[PHONE]", text)


def redact_home_username(text: str) -> str:
    text = _WIN_USER_RE.sub(lambda m: f"{m.group(1)}<user>{m.group(3)}", text)
    text = _NIX_USER_RE.sub(lambda m: f"{m.group(1)}<user>{m.group(3)}", text)
    return text


def redact(text: str, *, secrets: list[str] | None = None) -> str:
    """전체 마스킹 파이프라인. `secrets`는 호출자가 이번 세션에서 쥐고 있던 원문 값들."""
    if not text:
        return text
    for secret in secrets or []:
        text = redact_known_secret(text, secret)
    text = redact_key_like_tokens(text)
    text = redact_phone_numbers(text)
    text = redact_home_username(text)
    return text

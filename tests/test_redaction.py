from __future__ import annotations

from leetkit_manager.redaction import redact, redact_home_username, redact_phone_numbers


def test_redact_masks_known_secret_verbatim():
    out = redact("license key is ABC123SECRET", secrets=["ABC123SECRET"])
    assert "ABC123SECRET" not in out
    assert "[REDACTED]" in out


def test_redact_masks_long_hex_token_like_dart_api_key():
    fake_key = "a" * 40
    out = redact(f"DART_API_KEY={fake_key}")
    assert fake_key not in out
    assert out.endswith(fake_key[-4:]) or fake_key[-4:] in out


def test_redact_masks_long_base32_token_like_license_key():
    fake_license = "A" * 60
    out = redact(f"key: {fake_license}")
    assert fake_license not in out


def test_redact_phone_numbers():
    out = redact_phone_numbers("연락처: 010-1234-5678 입니다")
    assert "010-1234-5678" not in out
    assert "[PHONE]" in out


def test_redact_home_username_windows_path():
    out = redact_home_username(r"C:\Users\johnhyeon\.telegramlens\session.session")
    assert "johnhyeon" not in out
    assert "<user>" in out


def test_redact_home_username_unix_path():
    out = redact_home_username("/home/johnhyeon/.telegramlens/session.session")
    assert "johnhyeon" not in out
    assert "<user>" in out


def test_redact_leaves_normal_text_alone():
    text = "StockLens 라이선스가 활성화되어 있습니다."
    assert redact(text) == text

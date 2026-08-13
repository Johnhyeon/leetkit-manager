from __future__ import annotations

from leetkit_manager.redaction import redact, redact_home_username, redact_phone_numbers


def test_redact_masks_known_secret_verbatim():
    out = redact("license key is ABC123SECRET", secrets=["ABC123SECRET"])
    assert "ABC123SECRET" not in out
    assert "[REDACTED]" in out


class TestSecretsSurviveLineWrapping:
    """실사용에서 확인된 유출(2026-08-13): Claude Desktop 로그가 긴 URL을 줄바꿈으로 접어
    40자 DART 키가 14자+26자로 갈라지자, "24자 이상 hex" 규칙이 앞 조각을 통째로 흘렸다."""

    def test_key_split_across_a_line_break_is_masked(self):
        head, tail = "3c209ef8fb073d", "aadf3bcee13811c7541af6cebf"
        out = redact(f"GET /api/company.json?crtfc_key={head}   \n        {tail}   ")
        assert head not in out, out
        assert tail not in out, out

    def test_param_name_wins_even_for_short_values(self):
        """길이 규칙에 못 미치는 값이라도 이름이 비밀이라고 말하면 지운다."""
        out = redact("?crtfc_key=abcd1234")
        assert "abcd1234" not in out

    def test_ordinary_text_is_left_alone(self):
        out = redact("종목코드=005930 이고 count=120 입니다")
        assert "005930" in out and "120" in out


class TestEscapedHomePaths:
    """로그에 JSON이 그대로 실리면 경로가 이스케이프된다. 실사용 로그에서 이 형태가
    3,749건, 단일 역슬래시가 178건이었는데 예전 규칙은 많은 쪽을 통째로 놓쳤다."""

    def test_json_escaped_windows_path(self):
        out = redact(r"{\"path\": \"C:\\Users\\whdqj\\AppData\"}")
        assert "whdqj" not in out, out

    def test_double_escaped_windows_path(self):
        """JSON 안에 또 JSON이 실리면 역슬래시가 넷이 된다 — 실제 mcp.log에 있는 형태."""
        out = redact(r"C:\\\\Users\\\\whdqj\\\\AppData")
        assert "whdqj" not in out, out

    def test_any_username_not_just_ours(self):
        """정규식이 특정 사용자명에 맞춰져 있으면 안 된다 — 남의 PC에서 돌아야 한다.
        (부분 문자열이 아니라 경로 전체를 비교한다 — "a" 같은 이름은 뒤 경로에도 들어간다)"""
        for name in ("kim", "박지훈", "user.name-2", "a", "Administrator"):
            assert redact(rf"C:\Users\{name}\AppData") == r"C:\Users\<user>\AppData", name

    def test_other_drive_letters(self):
        assert redact(r"D:\Users\kim\x") == r"D:\Users\<user>\x"

    def test_plain_windows_path_still_works(self):
        out = redact(r"C:\Users\whdqj\AppData")
        assert "whdqj" not in out, out

    def test_forward_slash_path_still_works(self):
        out = redact("C:/Users/whdqj/AppData")
        assert "whdqj" not in out, out


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

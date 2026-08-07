from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from leetkit_manager import review_prompt


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    """실제 홈 디렉터리의 상태 파일을 건드리지 않게 격리한다."""
    path = tmp_path / "review_prompt.json"
    with patch.object(review_prompt, "_state_path", return_value=path):
        yield path


def _config(**overrides) -> dict:
    config = dict(review_prompt._DEFAULTS)
    config.update({"enabled": True, "url": "https://forms.example/review"})
    config.update(overrides)
    return config


def _state(path, **fields) -> None:
    path.write_text(json.dumps(fields), encoding="utf-8")


def _eligible_state(path, now: float) -> None:
    """조건을 전부 만족하는 상태 — 각 테스트는 여기서 하나씩만 무너뜨린다."""
    _state(path, first_launch_at=now - 30 * 86400, launches=10)


class TestGate:
    def test_shows_when_all_conditions_met(self, _isolated_state):
        now = time.time()
        _eligible_state(_isolated_state, now)
        prompt = review_prompt.pending_prompt(_config(), ready=True, now=now)
        assert prompt is not None
        assert prompt["url"] == "https://forms.example/review"

    def test_disabled_config_never_shows(self, _isolated_state):
        now = time.time()
        _eligible_state(_isolated_state, now)
        assert review_prompt.pending_prompt(_config(enabled=False), ready=True, now=now) is None

    def test_empty_url_never_shows(self, _isolated_state):
        """링크가 정해지기 전에 나간 빌드에서 모달이 뜨면 누를 데가 없다."""
        now = time.time()
        _eligible_state(_isolated_state, now)
        assert review_prompt.pending_prompt(_config(url=""), ready=True, now=now) is None

    def test_non_https_url_is_rejected(self, _isolated_state):
        """원격 파일이 브라우저를 여는 통로다 — https 아닌 스킴은 안 받는다."""
        now = time.time()
        _eligible_state(_isolated_state, now)
        for bad in ("http://forms.example", "file:///C:/x", "javascript:alert(1)"):
            assert review_prompt.pending_prompt(_config(url=bad), ready=True, now=now) is None

    def test_not_ready_never_shows(self, _isolated_state):
        """아직 설치 중이거나 문제를 고치는 중인 사람에게 후기를 달라고 하면 역효과다."""
        now = time.time()
        _eligible_state(_isolated_state, now)
        assert review_prompt.pending_prompt(_config(), ready=False, now=now) is None

    def test_first_launch_never_shows(self, _isolated_state):
        """써보지도 않은 사람에게 후기를 묻지 않는다 — 이 기능의 핵심 제약."""
        now = time.time()
        _state(_isolated_state, first_launch_at=now, launches=1)
        assert review_prompt.pending_prompt(_config(), ready=True, now=now) is None

    def test_too_few_launches_never_shows(self, _isolated_state):
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 30 * 86400, launches=2)
        assert review_prompt.pending_prompt(_config(min_launches=3), ready=True, now=now) is None

    def test_too_soon_after_install_never_shows(self, _isolated_state):
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 2 * 86400, launches=10)
        assert review_prompt.pending_prompt(_config(min_days=7), ready=True, now=now) is None

    def test_missing_state_file_never_shows(self, _isolated_state):
        """상태 파일이 없으면 first_launch_at도 없다 = 지금이 첫 실행."""
        assert review_prompt.pending_prompt(_config(), ready=True) is None

    def test_corrupt_state_file_never_shows(self, _isolated_state):
        _isolated_state.write_text("not json", encoding="utf-8")
        assert review_prompt.pending_prompt(_config(), ready=True) is None


class TestSnoozeAndStop:
    def test_recent_ask_is_snoozed(self, _isolated_state):
        now = time.time()
        _state(
            _isolated_state,
            first_launch_at=now - 30 * 86400,
            launches=10,
            asks=1,
            last_ask_at=now - 3 * 86400,
        )
        assert review_prompt.pending_prompt(_config(snooze_days=14), ready=True, now=now) is None

    def test_asks_again_after_snooze_period(self, _isolated_state):
        now = time.time()
        _state(
            _isolated_state,
            first_launch_at=now - 60 * 86400,
            launches=10,
            asks=1,
            last_ask_at=now - 20 * 86400,
        )
        assert review_prompt.pending_prompt(_config(snooze_days=14), ready=True, now=now) is not None

    def test_stops_after_max_asks(self, _isolated_state):
        """몇 번 물어봤는데 안 남겼으면 그 사람은 안 남기는 거다 — 계속 묻지 않는다."""
        now = time.time()
        _state(
            _isolated_state,
            first_launch_at=now - 200 * 86400,
            launches=50,
            asks=3,
            last_ask_at=now - 100 * 86400,
        )
        assert review_prompt.pending_prompt(_config(max_asks=3), ready=True, now=now) is None

    def test_done_never_shows_again(self, _isolated_state):
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 30 * 86400, launches=10, done=True)
        assert review_prompt.pending_prompt(_config(), ready=True, now=now) is None

    def test_mark_done_stops_future_prompts(self, _isolated_state):
        now = time.time()
        _eligible_state(_isolated_state, now)
        assert review_prompt.pending_prompt(_config(), ready=True, now=now) is not None
        review_prompt.mark_done()
        assert review_prompt.pending_prompt(_config(), ready=True, now=now) is None

    def test_mark_asked_records_count_and_time(self, _isolated_state):
        now = time.time()
        _eligible_state(_isolated_state, now)
        review_prompt.mark_asked(now=now)
        state = json.loads(_isolated_state.read_text(encoding="utf-8"))
        assert state["asks"] == 1
        assert state["last_ask_at"] == now
        # 방금 물어봤으니 바로 또 뜨면 안 된다
        assert review_prompt.pending_prompt(_config(), ready=True, now=now) is None


class TestRecordLaunch:
    def test_first_call_sets_baseline(self, _isolated_state):
        review_prompt.record_launch()
        state = json.loads(_isolated_state.read_text(encoding="utf-8"))
        assert state["launches"] == 1
        assert state["first_launch_at"] > 0

    def test_repeated_calls_keep_original_first_launch(self, _isolated_state):
        """first_launch_at이 매번 갱신되면 "설치 후 N일" 조건이 영영 성립하지 않는다."""
        review_prompt.record_launch()
        first = json.loads(_isolated_state.read_text(encoding="utf-8"))["first_launch_at"]
        review_prompt.record_launch()
        state = json.loads(_isolated_state.read_text(encoding="utf-8"))
        assert state["launches"] == 2
        assert state["first_launch_at"] == first

    def test_survives_unwritable_state_file(self, _isolated_state):
        """상태를 못 적어도 앱은 떠야 한다 — 후기 요청 하나 때문에 실행이 막히면 안 된다."""
        with patch.object(review_prompt.Path, "write_text", side_effect=OSError("읽기 전용")):
            review_prompt.record_launch()  # 예외가 새어나오면 실패


class TestFetchConfig:
    def test_remote_values_override_defaults(self):
        remote = {"enabled": True, "url": "https://x/y", "min_days": 1}
        with patch("httpx.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.json.return_value = remote
            config = review_prompt.fetch_config()
        assert config["enabled"] is True
        assert config["url"] == "https://x/y"
        assert config["min_days"] == 1
        assert config["cta"] == review_prompt._DEFAULTS["cta"]  # 안 준 값은 기본값 유지

    def test_network_failure_falls_back_to_disabled(self):
        """오프라인 사용자에게 오류를 보여줄 이유가 없다 — 그냥 안 띄운다."""
        with patch("httpx.get", side_effect=Exception("offline")):
            assert review_prompt.fetch_config()["enabled"] is False

    def test_garbage_response_falls_back_to_disabled(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.json.return_value = ["not", "a", "dict"]
            assert review_prompt.fetch_config()["enabled"] is False

    def test_unknown_remote_keys_are_ignored(self):
        """원격 파일에 주석용 키(_읽는_곳 등)가 들어 있어도 무해해야 한다."""
        with patch("httpx.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.json.return_value = {"_읽는_곳": "설명", "enabled": True}
            config = review_prompt.fetch_config()
        assert "_읽는_곳" not in config
        assert config["enabled"] is True

    def test_shipped_config_file_is_valid_and_off_by_default(self):
        """리포에 커밋된 파일이 곧 배포되는 설정이다 — 깨져 있으면 전부 무력화된다."""
        from pathlib import Path

        path = Path(__file__).parent.parent / "review_prompt.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["enabled"] is False, "링크를 정하기 전에는 꺼진 채로 나가야 한다"
        for key in ("url", "title", "body", "cta", "min_days", "min_launches", "snooze_days", "max_asks"):
            assert key in data

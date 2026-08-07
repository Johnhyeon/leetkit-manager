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
    """조건을 전부 만족하는 상태 — 각 테스트는 여기서 하나씩만 무너뜨린다.
    후기 기간(deadline_days) 안쪽이어야 한다 — 밖이면 다른 조건과 무관하게 안 뜬다."""
    _state(path, first_launch_at=now - 10 * 86400, launches=10)


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

    def test_empty_url_still_shows_as_guidance_only(self, _isolated_state):
        """리틀리 후기란은 구매자마다 주소가 달라(구매 확인 메일의 '파일보기' 링크)
        앱이 링크로 보낼 수 없다 — 그 경우엔 버튼 없이 안내만 띄운다."""
        now = time.time()
        _eligible_state(_isolated_state, now)
        prompt = review_prompt.pending_prompt(_config(url=""), ready=True, now=now)
        assert prompt is not None
        assert prompt["url"] == ""

    def test_non_https_url_is_dropped_but_guidance_remains(self, _isolated_state):
        """원격 파일이 브라우저를 여는 통로다 — https 아닌 스킴은 주소로 안 쓴다.
        그렇다고 모달까지 없애면 안내가 통째로 사라지므로, 버튼만 빠진다."""
        now = time.time()
        _eligible_state(_isolated_state, now)
        for bad in ("http://forms.example", "file:///C:/x", "javascript:alert(1)"):
            prompt = review_prompt.pending_prompt(_config(url=bad), ready=True, now=now)
            assert prompt is not None
            assert prompt["url"] == ""

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
        _state(_isolated_state, first_launch_at=now - 10 * 86400, launches=2)
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
            first_launch_at=now - 10 * 86400,
            launches=10,
            asks=1,
            last_ask_at=now - 3 * 86400,
        )
        assert review_prompt.pending_prompt(_config(snooze_days=7), ready=True, now=now) is None

    def test_asks_again_after_snooze_period(self, _isolated_state):
        now = time.time()
        _state(
            _isolated_state,
            first_launch_at=now - 18 * 86400,
            launches=10,
            asks=1,
            last_ask_at=now - 10 * 86400,
        )
        assert review_prompt.pending_prompt(_config(snooze_days=7), ready=True, now=now) is not None

    def test_stops_after_max_asks(self, _isolated_state):
        """몇 번 물어봤는데 안 남겼으면 그 사람은 안 남기는 거다 — 계속 묻지 않는다."""
        now = time.time()
        _state(
            _isolated_state,
            first_launch_at=now - 18 * 86400,
            launches=50,
            asks=3,
            last_ask_at=now - 10 * 86400,
        )
        assert review_prompt.pending_prompt(_config(max_asks=3), ready=True, now=now) is None

    def test_done_never_shows_again(self, _isolated_state):
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 10 * 86400, launches=10, done=True)
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
        assert data["enabled"] is False, "문구를 확정하기 전에는 꺼진 채로 나가야 한다"
        for key in ("url", "title", "body", "cta", "min_days", "min_launches", "snooze_days", "max_asks"):
            assert key in data


class TestReviewWindowDeadline:
    """리틀리 후기는 파일 받는 기간(구매 후 약 한 달)이 만료되면 후기란까지 사라진다 —
    지난 뒤의 요청은 누를 데 없는 안내가 되므로 아예 묻지 않아야 한다."""

    def test_stops_asking_after_the_deadline(self, _isolated_state):
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 25 * 86400, launches=10)
        assert review_prompt.pending_prompt(_config(deadline_days=21), ready=True, now=now) is None

    def test_still_asks_just_inside_the_deadline(self, _isolated_state):
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 20 * 86400, launches=10)
        assert (
            review_prompt.pending_prompt(_config(deadline_days=21), ready=True, now=now) is not None
        )

    def test_rare_user_who_qualifies_too_late_is_never_asked(self, _isolated_state):
        """Manager는 매일 여는 앱이 아니다. 설치하고 한참 뒤에야 두 번째로 열면
        실행 횟수 조건은 그때 채워지지만, 그때는 이미 후기를 남길 수 없다."""
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 40 * 86400, launches=2)
        assert review_prompt.pending_prompt(_config(), ready=True, now=now) is None

    def test_shipped_schedule_fits_entirely_inside_the_window(self):
        """배포되는 값이 기한을 넘기면 마지막 요청이 통째로 헛돈다 — 예전 값
        (7일 시작·14일 간격·3회)은 3번째가 35일째라 실제로 그랬다."""
        from pathlib import Path

        config = json.loads(
            (Path(__file__).parent.parent / "review_prompt.json").read_text(encoding="utf-8")
        )
        last_ask_day = config["min_days"] + config["snooze_days"] * (config["max_asks"] - 1)
        assert last_ask_day <= config["deadline_days"], (
            f"마지막 요청이 {last_ask_day}일째라 마감선({config['deadline_days']}일)을 넘는다"
        )

    def test_defaults_also_fit_inside_the_window(self):
        """원격 설정을 못 받았을 때 쓰이는 코드 기본값도 같은 조건을 지켜야 한다."""
        d = review_prompt._DEFAULTS
        last_ask_day = d["min_days"] + d["snooze_days"] * (d["max_asks"] - 1)
        assert last_ask_day <= d["deadline_days"]


class TestDeadlineNote:
    """"앞으로 며칠 남았다" 안내 — 실제 규칙(구매 후 약 한 달)과 우리가 센 기준
    (설치일)을 둘 다 밝혀야 한다. 앱은 구매 시각을 알 수 없어 첫 실행 시각으로
    대신 세는데, 기준을 안 밝히면 늦게 설치한 사람에게 없는 기간을 있다고 말하게 된다."""

    def test_counts_down_from_first_launch(self, _isolated_state):
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 10 * 86400, launches=10)
        note = review_prompt.pending_prompt(
            _config(review_window_days=30), ready=True, now=now
        )["deadline_note"]
        assert "**20일** 남았습니다" in note, "숫자는 굵게 — 눈에 걸려야 하는 건 이것뿐이다"

    def test_states_both_the_rule_and_the_basis(self, _isolated_state):
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 5 * 86400, launches=10)
        note = review_prompt.pending_prompt(
            _config(review_window_days=30), ready=True, now=now
        )["deadline_note"]
        assert "구매 후 약 **30일**" in note, "실제 규칙을 밝혀야 늦게 설치한 사람이 보정할 수 있다"
        assert "설치하신 날부터" in note, "우리가 센 기준을 밝혀야 한다"

    def test_can_be_turned_off(self, _isolated_state):
        """기간 정책이 바뀌면 숫자를 고치는 대신 안내 자체를 끌 수 있어야 한다."""
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 5 * 86400, launches=10)
        prompt = review_prompt.pending_prompt(_config(review_window_days=0), ready=True, now=now)
        assert prompt["deadline_note"] == ""

    def test_no_note_once_the_window_has_passed(self, _isolated_state):
        """창이 닫혔는데 "0일 남았습니다"를 띄우면 안내가 아니라 조롱이 된다.
        (물어보는 것 자체도 deadline_days에서 이미 막히지만, 창을 짧게 설정해
        두 값이 엇갈려도 여기서 한 번 더 막힌다.)"""
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 15 * 86400, launches=10)
        prompt = review_prompt.pending_prompt(
            _config(review_window_days=10, deadline_days=21), ready=True, now=now
        )
        assert prompt["deadline_note"] == ""

    def test_shipped_window_is_longer_than_the_ask_deadline(self):
        """물어보기를 멈추는 시점(deadline_days)이 알려주는 기한(review_window_days)보다
        길면, 마지막 요청에서 "0일 남았습니다"가 나온다."""
        from pathlib import Path

        config = json.loads(
            (Path(__file__).parent.parent / "review_prompt.json").read_text(encoding="utf-8")
        )
        assert config["deadline_days"] < config["review_window_days"]


class TestEmphasisMarkup:
    """**…**는 app.js의 renderEmphasis가 <strong>으로 바꾼다. 표시를 짝이 안 맞게
    남기면 별표가 화면에 그대로 보인다."""

    def test_deadline_note_marks_only_the_numbers(self, _isolated_state):
        now = time.time()
        _state(_isolated_state, first_launch_at=now - 5 * 86400, launches=10)
        note = review_prompt.pending_prompt(_config(), ready=True, now=now)["deadline_note"]
        assert note.count("**") == 4, "여는/닫는 표시가 짝이 맞아야 한다(숫자 두 곳)"

    def test_shipped_body_has_balanced_markers(self):
        from pathlib import Path

        config = json.loads(
            (Path(__file__).parent.parent / "review_prompt.json").read_text(encoding="utf-8")
        )
        assert config["body"].count("**") % 2 == 0

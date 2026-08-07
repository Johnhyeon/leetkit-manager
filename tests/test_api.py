from __future__ import annotations

from unittest.mock import MagicMock, patch

from leetkit_manager import orchestrator, review_prompt, shortcut
from leetkit_manager.lens_contract import STOCKLENS
from leetkit_manager.process_runner import ProcessResult
from leetkit_manager.ui.api import Api, _diagnosis_to_dict, _first_meaningful_line


class TestProblemDetail:
    """"호환되지 않는 Lens 버전"은 원인을 통째로 삼키던 라벨이었다 — 사용자도 지원하는
    쪽도 왜 그런지 알 수 없어 "업데이트해도 그대로"에 갇혔다."""

    def _diagnose_with(self, process):
        with patch.object(orchestrator, "run_json_cli", return_value=(process, None)):
            return orchestrator.diagnose_lens(STOCKLENS)

    def test_none_when_healthy(self):
        payload = {"schema_version": 1, "product": "stocklens", "package_name": "stocklens-mcp",
                   "installed_version": "1.0.0", "overall": "ok", "checks": [], "targets": []}
        with patch.object(orchestrator, "run_json_cli", return_value=(_ok_process(), payload)):
            diag = orchestrator.diagnose_lens(STOCKLENS)
        assert _diagnosis_to_dict(diag)["problem_detail"] is None

    def test_includes_exit_code_and_actual_output(self):
        process = ProcessResult(
            cmd=["x"], exit_code=2, timed_out=False, duration_s=0.1, stdout="",
            stderr="usage: stocklens-doctor [-h]\nerror: unrecognized arguments: --json",
        )
        detail = _diagnosis_to_dict(self._diagnose_with(process))["problem_detail"]
        assert "2" in detail
        assert "usage: stocklens-doctor" in detail
        assert "삭제" in detail  # 다음에 할 일까지 안내

    def test_skips_decorative_separator_lines(self):
        """옛 버전 doctor는 맨 위에 `====` 구분선부터 찍는다 — 그걸 보여주면
        아무 정보가 안 된다."""
        process = ProcessResult(
            cmd=["x"], exit_code=1, timed_out=False, duration_s=0.1,
            stdout="============\n\n  StockLens Doctor - Installation Diagnosis\n", stderr="",
        )
        detail = _diagnosis_to_dict(self._diagnose_with(process))["problem_detail"]
        assert "StockLens Doctor" in detail
        assert "====" not in detail

    def test_mentions_timeout_when_it_timed_out(self):
        process = ProcessResult(
            cmd=["x"], exit_code=None, timed_out=True, duration_s=30.0,
            stdout="", stderr="", error="timeout",
        )
        detail = _diagnosis_to_dict(self._diagnose_with(process))["problem_detail"]
        assert "제한 시간" in detail

    def test_output_is_redacted(self):
        """이 문구는 화면에 뜨고 진단 복사로도 나간다 — 키·경로가 그대로 실리면 안 된다."""
        process = ProcessResult(
            cmd=["x"], exit_code=1, timed_out=False, duration_s=0.1, stdout="",
            stderr=r"failed at C:\Users\johndoe\.stocklens key=0123456789abcdef0123456789abcdef01234567",
        )
        detail = _diagnosis_to_dict(self._diagnose_with(process))["problem_detail"]
        assert "johndoe" not in detail
        assert "0123456789abcdef0123456789abcdef01234567" not in detail

    def test_first_meaningful_line_ignores_blank_and_symbol_only(self):
        assert _first_meaningful_line("\n\n---\n***\n실제 내용\n") == "실제 내용"
        assert _first_meaningful_line("") is None
        assert _first_meaningful_line(None) is None


def _ok_process() -> ProcessResult:
    return ProcessResult(cmd=["x"], exit_code=0, stdout="", stderr="", timed_out=False, duration_s=0.1)


class TestChooseShortcutLocation:
    def test_success_marks_offered(self, tmp_path):
        marker = tmp_path / "shortcut_created"
        fake_window = MagicMock()
        fake_window.create_file_dialog.return_value = [str(tmp_path)]
        link_path = tmp_path / "LeetKit Manager.lnk"

        with patch.object(shortcut, "_MARKER", marker), \
             patch("webview.windows", [fake_window]), \
             patch.object(shortcut, "create_shortcut_at", return_value=link_path):
            result = Api().choose_shortcut_location()
            offered = shortcut.has_shortcut_been_offered()

        assert result["ok"] is True
        assert offered is True

    def test_failure_does_not_mark_offered(self, tmp_path):
        """실사용 중 발견된 문제 재현: 바로가기 생성이 실패했는데도 "물어봤다"로
        기록해버리면, 원인을 고친 뒤에도 has_shortcut_been_offered() 가드에 막혀
        영영 재시도가 안 된다 — 실패했을 때는 다음 실행에서 다시 시도할 수 있어야
        한다."""
        marker = tmp_path / "shortcut_created"
        fake_window = MagicMock()
        fake_window.create_file_dialog.return_value = [str(tmp_path)]

        with patch.object(shortcut, "_MARKER", marker), \
             patch("webview.windows", [fake_window]), \
             patch.object(shortcut, "create_shortcut_at", return_value=None):
            result = Api().choose_shortcut_location()
            offered = shortcut.has_shortcut_been_offered()

        assert result["ok"] is False
        assert offered is False

    def test_already_offered_skips_dialog_entirely(self, tmp_path):
        marker = tmp_path / "shortcut_created"
        with patch.object(shortcut, "_MARKER", marker):
            shortcut.mark_shortcut_offered()

            fake_window = MagicMock()
            with patch("webview.windows", [fake_window]):
                result = Api().choose_shortcut_location()

        assert result["ok"] is True
        fake_window.create_file_dialog.assert_not_called()


class TestReviewPrompt:
    """후기 요청의 브릿지 계층 — 판정 자체는 test_review_prompt.py가 다룬다."""

    def test_returns_none_and_clears_url_when_not_due(self):
        api = Api()
        api._review_url = "https://stale.example"
        with patch.object(review_prompt, "fetch_config", return_value={}), \
             patch.object(review_prompt, "pending_prompt", return_value=None):
            assert api.review_prompt(True) is None
        # 지난번에 받아둔 주소가 남아 있으면, 모달이 안 떴는데도 열 수 있는 상태가 된다
        assert api._review_url is None

    def test_url_is_not_exposed_to_javascript(self):
        """JS는 주소를 받지도, 넘기지도 않는다 — 임의 URL을 여는 통로가 생기지 않게."""
        pending = {"title": "t", "body": "b", "cta": "c", "url": "https://forms.example"}
        api = Api()
        with patch.object(review_prompt, "fetch_config", return_value={}), \
             patch.object(review_prompt, "pending_prompt", return_value=pending), \
             patch.object(review_prompt, "mark_asked"):
            result = api.review_prompt(True)
        assert "url" not in result
        assert api._review_url == "https://forms.example"

    def test_showing_the_modal_counts_as_an_ask(self):
        """세지 않으면 스누즈·최대 횟수가 전부 무력화돼 매번 뜬다."""
        pending = {"title": "t", "body": "b", "cta": "c", "url": "https://forms.example"}
        with patch.object(review_prompt, "fetch_config", return_value={}), \
             patch.object(review_prompt, "pending_prompt", return_value=pending), \
             patch.object(review_prompt, "mark_asked") as mock_asked:
            Api().review_prompt(True)
        mock_asked.assert_called_once()

    def test_network_failure_is_silent(self):
        with patch.object(review_prompt, "fetch_config", side_effect=Exception("offline")):
            assert Api().review_prompt(True) is None

    def test_open_without_a_pending_prompt_does_nothing(self):
        api = Api()
        with patch("webbrowser.open") as mock_open, \
             patch.object(review_prompt, "mark_done") as mock_done:
            assert api.open_review_url() is False
        mock_open.assert_not_called()
        mock_done.assert_not_called()

    def test_open_uses_cached_url_and_stops_asking(self):
        api = Api()
        api._review_url = "https://forms.example"
        with patch("webbrowser.open") as mock_open, \
             patch.object(review_prompt, "mark_done") as mock_done:
            assert api.open_review_url() is True
        mock_open.assert_called_once_with("https://forms.example")
        mock_done.assert_called_once()

    def test_never_again_stops_asking(self):
        with patch.object(review_prompt, "mark_done") as mock_done:
            assert Api().review_prompt_never_again() is True
        mock_done.assert_called_once()

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

    def test_already_offered_with_the_shortcut_still_there_does_nothing(self, tmp_path):
        marker = tmp_path / "shortcut_created"
        existing = tmp_path / "LeetKit Manager.lnk"
        with patch.object(shortcut, "_MARKER", marker), \
             patch.object(shortcut, "existing_shortcut", return_value=existing), \
             patch.object(shortcut, "create_shortcut_at") as mock_create:
            shortcut.mark_shortcut_offered()

            fake_window = MagicMock()
            with patch("webview.windows", [fake_window]):
                result = Api().choose_shortcut_location()

        assert result["ok"] is True
        assert result["path"] == str(existing)
        fake_window.create_file_dialog.assert_not_called()
        mock_create.assert_not_called()

    def test_already_offered_but_shortcut_gone_recreates_it(self, tmp_path):
        """표시만 보고 건너뛰면 바로가기가 없는데도 성공이라고 답한다 — 사용자가 지웠거나
        생성이 실패한 경우 다시 만들 방법이 사라진다(맥에서 실제로 겪었다). 위치는 이미
        답한 질문이므로 다이얼로그로 또 묻지 않고 그때 고른 폴더에 다시 만든다."""
        marker = tmp_path / "shortcut_created"
        chosen = tmp_path / "내 앱"
        chosen.mkdir()
        made = chosen / "LeetKit Manager.lnk"
        with patch.object(shortcut, "_MARKER", marker), \
             patch.object(shortcut, "existing_shortcut", return_value=None), \
             patch.object(shortcut, "create_shortcut_at", return_value=made) as mock_create:
            shortcut.mark_shortcut_offered(chosen)

            fake_window = MagicMock()
            with patch("webview.windows", [fake_window]):
                result = Api().choose_shortcut_location()

        assert result["ok"] is True
        assert result["path"] == str(made)
        fake_window.create_file_dialog.assert_not_called()  # 위치는 다시 안 묻는다
        mock_create.assert_called_once_with(chosen)


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

    def test_guidance_only_prompt_reports_no_url(self):
        """리틀리 후기란처럼 링크를 만들 수 없는 경우 — JS가 버튼을 감추도록 알려준다."""
        pending = {"title": "t", "body": "b", "cta": "c", "url": ""}
        api = Api()
        with patch.object(review_prompt, "fetch_config", return_value={}), \
             patch.object(review_prompt, "pending_prompt", return_value=pending), \
             patch.object(review_prompt, "mark_asked"):
            result = api.review_prompt(True)
        assert result["has_url"] is False
        assert api._review_url is None

    def test_prompt_with_url_reports_has_url(self):
        pending = {"title": "t", "body": "b", "cta": "c", "url": "https://forms.example"}
        api = Api()
        with patch.object(review_prompt, "fetch_config", return_value={}), \
             patch.object(review_prompt, "pending_prompt", return_value=pending), \
             patch.object(review_prompt, "mark_asked"):
            result = api.review_prompt(True)
        assert result["has_url"] is True


class TestPurchasePage:
    def test_purchase_url_matches_the_lenses(self):
        """CLI 활성화 안내(각 Lens licensing.py의 PURCHASE_URL)와 Manager 모달이
        다른 주소를 가리키면, 같은 제품인데 경로에 따라 다른 곳으로 보내게 된다."""
        from leetkit_manager.ui.api import PURCHASE_URL

        assert PURCHASE_URL == "https://litt.ly/leetkey_lab/sale/hzGHnRY"

    def test_purchase_url_is_in_the_open_url_allowlist(self):
        from leetkit_manager.ui.api import _ALLOWED_EXTERNAL_URLS, PURCHASE_URL

        assert PURCHASE_URL in _ALLOWED_EXTERNAL_URLS

    def test_open_purchase_page_opens_the_browser(self):
        from leetkit_manager.ui.api import PURCHASE_URL

        with patch("webbrowser.open") as mock_open:
            assert Api().open_purchase_page() is True
        mock_open.assert_called_once_with(PURCHASE_URL)


class TestInstallFailureReason:
    """예전엔 ok=False만 돌려줘서 화면에 "실패했습니다"밖에 못 띄웠다 — 사용자도 우리도
    원인을 알 방법이 없었다(맥에서 업데이트가 계속 실패했는데 단서가 하나도 없었다)."""

    def _proc(self, **kwargs):
        base = dict(cmd=["uv"], exit_code=1, stdout="", stderr="", timed_out=False, duration_s=0.0)
        base.update(kwargs)
        return ProcessResult(**base)

    def test_timeout_says_it_took_too_long(self):
        from leetkit_manager.ui.api import _install_failure_reason

        reason = _install_failure_reason(self._proc(timed_out=True))
        assert "시간" in reason

    def test_uses_the_stderr_line_uv_printed(self):
        """uv는 이유를 stderr에 또렷하게 적는다 — 그대로 보여주는 게 제일 낫다."""
        from leetkit_manager.ui.api import _install_failure_reason

        proc = self._proc(stderr="error: failed to remove directory ... 액세스가 거부되었습니다")
        assert "액세스가 거부" in _install_failure_reason(proc)

    def test_falls_back_to_stdout(self):
        from leetkit_manager.ui.api import _install_failure_reason

        assert "디스크" in _install_failure_reason(self._proc(stdout="error: 디스크 공간이 부족합니다"))

    def test_version_not_on_the_index_gets_a_useful_message(self):
        """uv 원문("no solution found")만 보여주면 뭘 하라는 건지 알 수 없다 — 기다리면
        풀린다는 걸 말해줘야 한다. 최신 판단을 simple 인덱스로 옮겨서 이제 거의 안
        생기지만, 다른 경로로 걸릴 때를 위한 안전망이다."""
        from leetkit_manager.ui.api import _install_failure_reason

        reason = _install_failure_reason(self._proc(stderr="error: No solution found when resolving"))
        assert "몇 분 뒤" in reason

    def test_returns_none_when_there_is_nothing_to_say(self):
        """할 말이 없으면 지어내지 않는다 — 화면이 "원인을 알 수 없습니다"로 정직하게 뜬다."""
        from leetkit_manager.ui.api import _install_failure_reason

        assert _install_failure_reason(self._proc()) is None
        assert _install_failure_reason(None) is None

    def test_install_or_update_carries_the_reason(self):
        from leetkit_manager.lens_contract import STOCKLENS

        failed = MagicMock()
        failed.ok = False
        failed.rollback_command = "uv tool install stocklens-mcp==0.5.8"
        failed.install = self._proc(stderr="error: 무언가 잘못됨")

        with patch.object(orchestrator, "diagnose_lens") as mock_diag, \
             patch.object(orchestrator, "update_lens", return_value=failed):
            mock_diag.return_value.report.latest_version = "0.5.9"
            mock_diag.return_value.report.installed_version = "0.5.8"
            result = Api().install_or_update(STOCKLENS.name)

        assert result["ok"] is False
        assert "무언가 잘못됨" in result["error"]


def test_install_timeout_is_generous_enough_for_the_biggest_lens():
    """120초는 실제로 부족했다 — TelegramLens는 telethon·Pillow·pystray까지 받아야 해서
    느린 회선이나 가상머신에서 중간에 잘렸다."""
    from leetkit_manager import package_service

    assert package_service._INSTALL_TIMEOUT >= 300

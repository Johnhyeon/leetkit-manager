from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from leetkit_manager import support_bundle


@pytest.fixture(autouse=True)
def _no_real_diagnosis():
    """요약 만들려고 실제 Lens를 부르면 테스트가 느리고 이 PC 상태에 휘둘린다."""
    with patch.object(support_bundle, "_summary_text", return_value="요약"):
        yield


@pytest.fixture(autouse=True)
def _empty_sources(tmp_path, monkeypatch):
    """이 PC에 실제로 깔린 Lens 로그가 결과에 섞이지 않게 전부 빈 곳으로 돌린다."""
    for env in ("DARTLENS_HOME", "TELEGRAMLENS_HOME"):
        monkeypatch.setenv(env, str(tmp_path / env))
    monkeypatch.setattr(support_bundle, "_stocklens_logs_dir", lambda: tmp_path / "nope")
    monkeypatch.setattr(support_bundle, "_claude_desktop_logs_dir", lambda: tmp_path / "nope2")


class TestDestinationFallback:
    """macOS는 바탕화면을 권한(TCC)으로 막는다. 거기서 막혔다고 번들 자체를 못 만들면,
    정작 도움이 필요한 사람이 도움을 요청할 방법을 잃는다."""

    def test_uses_the_desktop_when_writable(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / "Desktop").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        assert support_bundle._writable_dest_dir() == home / "Desktop"

    def test_falls_back_to_home_when_the_desktop_is_blocked(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        real_write = Path.write_text

        def blocked(self, *a, **k):
            if "Desktop" in str(self):
                raise PermissionError("Operation not permitted")
            return real_write(self, *a, **k)

        with patch.object(Path, "write_text", blocked):
            assert support_bundle._writable_dest_dir() == home

    def test_still_produces_a_bundle_when_the_desktop_is_blocked(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        real_write = Path.write_text

        def blocked(self, *a, **k):
            if "Desktop" in str(self):
                raise PermissionError("Operation not permitted")
            return real_write(self, *a, **k)

        with patch.object(Path, "write_text", blocked):
            zip_path = support_bundle.create_bundle()
        assert zip_path.is_file()
        assert "Desktop" not in str(zip_path)


class TestUnreadableSourcesAreRecorded:
    """로그가 없는 게 "문제가 없어서"인지 "못 읽어서"인지 구분이 안 되면 헛다리를 짚는다."""

    def test_permission_error_is_written_down(self, tmp_path):
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        notes: list[str] = []
        with patch.object(Path, "iterdir", side_effect=PermissionError("nope")):
            assert support_bundle._probe("StockLens 로그", blocked, notes) is False
        assert notes and "권한이 없어 못 읽음" in notes[0]

    def test_missing_folder_is_written_down_differently(self, tmp_path):
        notes: list[str] = []
        assert support_bundle._probe("StockLens 로그", tmp_path / "없음", notes) is False
        assert notes and "폴더 없음" in notes[0]

    def test_readable_folder_adds_no_note(self, tmp_path):
        notes: list[str] = []
        assert support_bundle._probe("StockLens 로그", tmp_path, notes) is True
        assert notes == []

    def test_notes_reach_the_summary(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / "Desktop").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        # 이 테스트만 진짜 요약을 쓴다 — notes가 실제로 실리는지가 요점이다.
        with patch.object(support_bundle, "_summary_text", wraps=support_bundle._summary_text) as spy, \
             patch.object(support_bundle.orchestrator, "run_full_diagnosis", return_value=[]), \
             patch.object(support_bundle, "_safe_files", side_effect=lambda notes: notes.append("- 못 읽음") or []):
            support_bundle.create_bundle()
        assert spy.call_args[0][0] == ["- 못 읽음"]


class TestRevealReportsWhatHappened:
    """예전엔 무조건 "폴더가 열렸습니다"라고 안내했다 — 안 열려도 그렇게 말하면
    사용자는 열리지도 않은 창을 찾는다."""

    def test_reports_success_on_macos(self, tmp_path):
        f = tmp_path / "a.zip"
        f.write_text("", encoding="utf-8")
        with patch.object(support_bundle.sys, "platform", "darwin"), \
             patch("subprocess.run") as run:
            run.return_value.returncode = 0
            assert support_bundle.reveal_in_file_manager(f) is True
        assert run.call_args[0][0][:2] == ["open", "-R"]

    def test_reports_failure_on_macos(self, tmp_path):
        f = tmp_path / "a.zip"
        f.write_text("", encoding="utf-8")
        with patch.object(support_bundle.sys, "platform", "darwin"), \
             patch("subprocess.run") as run:
            run.return_value.returncode = 1
            assert support_bundle.reveal_in_file_manager(f) is False

    def test_never_raises(self, tmp_path):
        with patch.object(support_bundle.sys, "platform", "darwin"), \
             patch("subprocess.run", side_effect=OSError("no open(1)")):
            assert support_bundle.reveal_in_file_manager(tmp_path / "a.zip") is False


class TestSecretsStayOut:
    """번들은 고객이 메일로 밖에 보내는 물건이다 — 여기 목록이 곧 유출 방지선이다."""

    def test_license_session_and_credentials_are_never_collected(self, tmp_path, monkeypatch):
        tl = tmp_path / "tl"
        tl.mkdir()
        for name in ("license.key", "session.session", "credentials.json", "daemon.log"):
            (tl / name).write_text("SECRET", encoding="utf-8")
        monkeypatch.setenv("TELEGRAMLENS_HOME", str(tl))
        collected = [arc for arc, _ in support_bundle._safe_files([])]
        assert any(a.endswith("daemon.log") for a in collected)
        for forbidden in ("license.key", "session.session", "credentials.json"):
            assert not any(forbidden in a for a in collected), forbidden

    def test_bundle_contents_match_the_allowlist(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / "Desktop").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        tl = tmp_path / "tl"
        tl.mkdir()
        (tl / "license.key").write_text("SECRET", encoding="utf-8")
        (tl / "daemon_status.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("TELEGRAMLENS_HOME", str(tl))

        zip_path = support_bundle.create_bundle()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert "summary.txt" in names
        assert "telegramlens/daemon_status.json" in names
        assert not any("license" in n for n in names)

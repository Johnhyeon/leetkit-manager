from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from leetkit_manager import support_bundle

# 아래 _no_real_diagnosis 픽스처가 모듈 속성을 모의객체로 바꾸기 전에 진짜 함수를
# 붙잡아 둔다 — 요약 본문 자체를 검사하는 테스트는 이걸 직접 부른다. 내부에서
# orchestrator 등은 호출 시점에 모듈에서 다시 찾으므로 패치는 그대로 먹는다.
_real_summary_text = support_bundle._summary_text


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
    # StockLens 로그는 새 위치(~/.stocklens/logs)와 옛 위치(Downloads/kstock/logs)
    # 둘 다 본다 — 이 PC의 진짜 로그가 섞이지 않게 양쪽 다 빈 곳으로 돌린다.
    monkeypatch.setattr(support_bundle, "_stocklens_logs_dirs", lambda: [tmp_path / "nope"])
    monkeypatch.setattr(support_bundle, "_claude_desktop_logs_dir", lambda: tmp_path / "nope2")
    # _desktop_dir 은 레지스트리에서 진짜 바탕화면을 읽는다 — 안 막으면 테스트가
    # 개발자 바탕화면에 zip을 뿌린다. 개별 테스트는 필요하면 다시 덮어쓴다.
    monkeypatch.setattr(support_bundle, "_desktop_dir", lambda: tmp_path / "desktop")


class TestDestinationFallback:
    """macOS는 바탕화면을 권한(TCC)으로 막는다. 거기서 막혔다고 번들 자체를 못 만들면,
    정작 도움이 필요한 사람이 도움을 요청할 방법을 잃는다."""

    def test_uses_the_desktop_when_writable(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / "Desktop").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        monkeypatch.setattr(support_bundle, "_desktop_dir", lambda: home / "Desktop")
        assert support_bundle._writable_dest_dir() == home / "Desktop"

    def test_follows_a_redirected_desktop(self, tmp_path, monkeypatch):
        """한국어 윈도우 + OneDrive면 바탕화면이 옮겨가고 예전 자리에 빈 껍데기가
        남는다. 거기 저장하면 성공은 하는데 사용자 눈에는 아무것도 안 보인다."""
        home = tmp_path / "home"
        (home / "Desktop").mkdir(parents=True)          # 껍데기 (쓰기도 된다)
        real = home / "OneDrive" / "바탕 화면"
        real.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        monkeypatch.setattr(support_bundle, "_desktop_dir", lambda: real)
        assert support_bundle._writable_dest_dir() == real

    def test_falls_back_to_home_when_the_desktop_is_blocked(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        # _desktop_dir 은 실제 레지스트리를 읽는다 — 안 막으면 이 PC의 진짜 바탕화면을
        # 반환해서 테스트가 이 컴퓨터 설정에 휘둘린다.
        monkeypatch.setattr(support_bundle, "_desktop_dir", lambda: home / "Desktop")
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
        monkeypatch.setattr(support_bundle, "_desktop_dir", lambda: home / "Desktop")
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


class TestSummaryCountsRecordedFailures:
    """실제 접수된 문의(2026-08-13)에서 나온 문제 — 네이버/DART에 연결이 전혀 안 되는
    PC가 보낸 번들의 summary.txt가 "정상 / 문제 없음"이었다. 같은 zip 안
    metrics_*.jsonl에는 ConnectError가 스무 건 넘게 있었는데 요약이 그걸 안 읽었다."""

    def _metrics(self, tmp_path: Path, name: str, rows: list[dict]) -> Path:
        f = tmp_path / name
        f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return f

    def test_connect_errors_are_counted_per_lens(self, tmp_path):
        sl = self._metrics(tmp_path, "sl.jsonl", [
            {"timestamp": "2026-08-13T21:49:57", "tool": "get_chart", "error": "ConnectError"},
            {"timestamp": "2026-08-13T22:15:10", "tool": "get_volume_ranking", "error": "ConnectError"},
            {"timestamp": "2026-08-13T22:04:59", "tool": "search_stock", "error": None},
        ])
        dl = self._metrics(tmp_path, "dl.jsonl", [
            {"timestamp": "2026-08-13T22:04:38", "tool": "search_company", "error": "ConnectError"},
        ])
        out = support_bundle._recent_call_failures([
            ("stocklens/metrics_20260813.jsonl", sl),
            ("dartlens/metrics_20260813.jsonl", dl),
        ])
        assert any("StockLens: 3건 중 2건 실패" in x and "ConnectError 2건" in x for x in out), out
        assert any("DartLens: 1건 중 1건 실패" in x for x in out), out
        # 마지막 실패가 언제 어느 도구였는지까지 있어야 로그를 안 열고도 시간대가 잡힌다.
        assert any("get_volume_ranking" in x for x in out), out

    def test_a_healthy_log_produces_no_line(self, tmp_path):
        ok = self._metrics(tmp_path, "sl.jsonl", [
            {"timestamp": "2026-08-13T22:04:59", "tool": "search_stock", "error": None},
        ])
        assert support_bundle._recent_call_failures([("stocklens/metrics_x.jsonl", ok)]) == []

    def test_broken_lines_do_not_sink_the_bundle(self, tmp_path):
        f = tmp_path / "sl.jsonl"
        f.write_text('{"tool": "a", "error": "ConnectError"}\n서걱\n[]\n', encoding="utf-8")
        out = support_bundle._recent_call_failures([("stocklens/metrics_x.jsonl", f)])
        assert any("1건 중 1건 실패" in x for x in out), out

    def test_non_metrics_files_are_ignored(self, tmp_path):
        f = tmp_path / "daemon_status.json"
        f.write_text('{"error": "ConnectError"}', encoding="utf-8")
        assert support_bundle._recent_call_failures([("telegramlens/daemon_status.json", f)]) == []

    def test_the_counts_reach_the_summary(self, tmp_path):
        sl = self._metrics(tmp_path, "sl.jsonl", [
            {"timestamp": "2026-08-13T21:49:57", "tool": "get_chart", "error": "ConnectError"},
        ])
        with patch.object(support_bundle.orchestrator, "run_full_diagnosis", return_value=[]):
            text = _real_summary_text([], [("stocklens/metrics_x.jsonl", sl)])
        assert "최근 도구 호출 실패" in text
        assert "StockLens: 1건 중 1건 실패" in text


class TestSummaryAsksWhetherDataCanBeFetched:
    """번들을 만드는 순간은 정의상 뭔가 안 되고 있는 때다 — 설치·설정만 보고
    "데이터를 가져올 수 있는가"를 안 물어보면 아무 문제도 못 찾는다."""

    def test_diagnosis_runs_online(self):
        with patch.object(support_bundle.orchestrator, "run_full_diagnosis", return_value=[]) as run:
            _real_summary_text([], [])
        assert run.call_args.kwargs["online"] is True

    def test_diagnosis_is_time_boxed(self):
        with patch.object(support_bundle.orchestrator, "run_full_diagnosis", return_value=[]) as run:
            _real_summary_text([], [])
        # 상한: 죽은 네트워크에서 번들 생성이 멈춘 것처럼 보이면 안 된다.
        # 하한: 실측 telegramlens-doctor 6.2초 × (--online 재시도로 2회) 보다 넉넉해야
        # 느린 PC에서 멀쩡한 Lens가 시간 초과로 찍히지 않는다.
        per_lens = run.call_args.kwargs["timeout"]
        assert per_lens >= 20, per_lens
        assert per_lens * len(support_bundle.LENSES) <= 80, per_lens


class TestClaudeMcpLogsAreFoundByKeyword:
    """실제 문의(2026-08-13)에서 나온 문제 — MCP 키를 `stocklens-mcp`로 등록한 PC의
    `mcp-server-stocklens-mcp.log`가 하드코딩 목록에 없어 통째로 빠졌다. 파일명은
    사용자가 정한 설정 키를 따르므로 이름을 맞히려 들면 계속 놓친다."""

    def _logs(self, tmp_path: Path, *names: str) -> Path:
        d = tmp_path / "logs"
        d.mkdir(exist_ok=True)
        for n in names:
            (d / n).write_text("x", encoding="utf-8")
        return d

    def test_config_key_variants_are_collected(self, tmp_path):
        d = self._logs(
            tmp_path,
            "mcp-server-stocklens-mcp.log",      # 예전 목록이 놓치던 이름
            "mcp-server-stocklens.log",
            "mcp-server-dartlens.log",
            "mcp-server-dart-mcp.log",
            "mcp-server-telegramlens.log",
            "mcp.log",
        )
        got = {p.name for p in support_bundle._claude_mcp_logs(d)}
        assert "mcp-server-stocklens-mcp.log" in got
        assert len(got) == 6, got

    def test_other_servers_are_left_alone(self, tmp_path):
        """남의 MCP 서버 로그까지 대신 내보낼 이유가 없다."""
        d = self._logs(tmp_path, "mcp-server-notion-personal.log", "mcp-server-stocklens.log")
        got = {p.name for p in support_bundle._claude_mcp_logs(d)}
        assert got == {"mcp-server-stocklens.log"}

    def test_non_log_files_are_skipped(self, tmp_path):
        d = self._logs(tmp_path, "mcp-server-stocklens.log.bak", "main.log")
        assert support_bundle._claude_mcp_logs(d) == []

    def test_unreadable_folder_is_not_fatal(self, tmp_path):
        with patch.object(Path, "iterdir", side_effect=OSError("nope")):
            assert support_bundle._claude_mcp_logs(tmp_path) == []

    def test_finding_nothing_is_written_down(self, tmp_path, monkeypatch):
        """폴더는 읽었는데 한 건도 못 알아본 경우와 파일이 원래 없는 경우를 구분해야 한다."""
        d = self._logs(tmp_path, "main.log")
        monkeypatch.setattr(support_bundle, "_claude_desktop_logs_dir", lambda: d)
        notes: list[str] = []
        support_bundle._safe_files(notes)
        assert any("해당하는 파일 없음" in n for n in notes), notes


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

    def test_windows_quotes_only_the_path(self):
        """탐색기는 `/select,` 와 경로를 한 인자로 묶어 통째로 따옴표를 씌우면 파싱하지
        못하고, 조용히 기본 폴더를 연다 — 사용자에겐 "폴더는 열렸는데 zip이 없다"로
        보인다. 경로에 공백이 없으면 우연히 동작해서 오래 안 걸렸다(한국어 윈도우 +
        OneDrive 바탕화면이 `...\\OneDrive\\바탕 화면`이라 거기서 드러났다).

        리스트로 넘기면 파이썬이 인자마다 따옴표를 씌우므로, 문자열로 넘겨야 한다."""
        path = Path(r"C:\Users\u\OneDrive\바탕 화면\leetkit-support-1.zip")
        with patch.object(support_bundle.sys, "platform", "win32"), \
             patch("subprocess.run") as run:
            assert support_bundle.reveal_in_file_manager(path) is True

        cmd = run.call_args[0][0]
        assert isinstance(cmd, str), "리스트로 넘기면 /select,경로 전체가 따옴표에 묶인다"
        assert cmd.startswith("explorer /select,\""), cmd
        assert cmd.endswith("\""), cmd
        assert str(path) in cmd


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


class TestSummaryNamesTheManagerVersion:
    """Lens 버전은 다 적으면서 정작 이 파일을 만든 프로그램의 버전만 빠져 있었다.
    받아보는 쪽이 "어떤 Manager를 쓰시나요"를 되물어야 하고, 그 왕복 한 번에 반나절이
    간다 — 그 버전에서 이미 고쳐진 문제인 경우가 흔하다."""

    def test_first_line_carries_the_version(self):
        from leetkit_manager import __version__

        # autouse 픽스처가 _summary_text 를 모의로 바꿔두므로 진짜 함수를 직접 부른다.
        with patch.object(support_bundle, "orchestrator") as orch:
            orch.run_full_diagnosis.return_value = []
            text = _real_summary_text([], [])

        assert __version__ in text.splitlines()[0]

    def test_unreadable_version_falls_back_instead_of_raising(self):
        """버전 한 줄 때문에 지원 파일 자체가 안 만들어지면 도움을 요청할 방법을 잃는다."""
        with patch.dict("sys.modules", {"leetkit_manager": None}):
            assert support_bundle._manager_version() == "?"

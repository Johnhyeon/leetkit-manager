"""DART API 키를 등록할 때 지금 등록된 MCP 대상을 빠짐없이 넘기는지.

실사용에서 나온 문제 — Codex에 등록해둔 사람이 매니저에서 키를 넣으면 Codex만 빠진
채로 끝났다. `--target`을 안 주면 Lens가 "auto"로 떨어지는데, auto는 `claude` CLI와
Claude Desktop 설정 폴더만 보고 **Codex는 판단 자체를 안 한다**(dartlens의
_resolve_targets). 키는 OS 자격 증명 저장소에 들어가서 동작은 하지만, 결과에 Codex가
안 잡혀 "등록 안 됨"으로 보인다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from leetkit_manager import orchestrator
from leetkit_manager.lens_contract import LENSES

DARTLENS = next(l for l in LENSES if l.name == "dartlens")


def _fake_run(recorder, payload=None):
    def run(cmd, *, timeout=None, input_text=None):
        recorder.append(cmd)
        process = type("P", (), {"error": None, "exit_code": 0})()
        return process, (payload or {"ok": True, "targets": [_target_of(cmd)], "api_key_saved": True})
    return run


def _target_of(cmd):
    return cmd[cmd.index("--target") + 1] if "--target" in cmd else "auto"


class TestTargetsArePassedThrough:
    def test_codex_alone_is_passed(self):
        calls = []
        with patch.object(orchestrator, "run_json_cli", side_effect=_fake_run(calls)):
            orchestrator.register_api_key(DARTLENS, "dart_api", "k", targets=["codex"])
        assert [_target_of(c) for c in calls] == ["codex"]

    def test_claude_pair_collapses_to_both(self):
        calls = []
        with patch.object(orchestrator, "run_json_cli", side_effect=_fake_run(calls)):
            orchestrator.register_api_key(
                DARTLENS, "dart_api", "k", targets=["claude-desktop", "claude-code"]
            )
        assert [_target_of(c) for c in calls] == ["both"]

    def test_claude_pair_plus_codex_does_not_drop_codex(self):
        """예전엔 targets를 받아도 "both" 하나로 접어서 codex가 통째로 버려졌다."""
        calls = []
        with patch.object(orchestrator, "run_json_cli", side_effect=_fake_run(calls)):
            result = orchestrator.register_api_key(
                DARTLENS, "dart_api", "k", targets=["claude-desktop", "claude-code", "codex"]
            )
        assert [_target_of(c) for c in calls] == ["both", "codex"]
        assert "codex" in result.targets

    def test_no_targets_falls_back_to_auto(self):
        """아직 아무 데도 등록 안 한 사람 — 등록이 먼저인 순서라 auto가 맞다."""
        calls = []
        with patch.object(orchestrator, "run_json_cli", side_effect=_fake_run(calls)):
            orchestrator.register_api_key(DARTLENS, "dart_api", "k", targets=None)
        assert len(calls) == 1
        assert "--target" not in calls[0]

    def test_key_never_appears_in_the_command_line(self):
        """키 원문은 stdin으로만. 인자에 실리면 프로세스 목록에 그대로 노출된다."""
        calls = []
        secret = "sekrit-dart-api-key"
        with patch.object(orchestrator, "run_json_cli", side_effect=_fake_run(calls)):
            orchestrator.register_api_key(DARTLENS, "dart_api", secret, targets=["codex"])
        for cmd in calls:
            assert secret not in " ".join(cmd)


class TestContract:
    def test_unsupported_credential_kind_is_refused(self):
        with pytest.raises(ValueError):
            orchestrator.register_api_key(DARTLENS, "telegram_login", "k")

    def test_not_installed_is_reported_clearly(self):
        def run(cmd, *, timeout=None, input_text=None):
            return type("P", (), {"error": "not_found", "exit_code": None})(), None

        with patch.object(orchestrator, "run_json_cli", side_effect=run):
            result = orchestrator.register_api_key(DARTLENS, "dart_api", "k", targets=["codex"])
        assert not result.ok
        assert result.error_code == "not_installed"

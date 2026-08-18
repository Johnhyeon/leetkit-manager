# -*- coding: utf-8 -*-
"""릴리스 전 실환경 검증.

가짜 홈/APPDATA 로 "ChatGPT 만 있는 PC" 등을 재현하고, 각 Lens 의 **실제 CLI**를 돌려서
(1) 어디에 등록되는지 (2) 파일이 진짜 쓰였는지 (3) doctor 가 그걸 알아보는지
(4) Manager 가 그 JSON 을 어떻게 읽는지 까지 끝까지 확인한다. 모의(mock) 아님.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("D:/project/stocklens")
LENSES = [
    # (표시명, 레포, 모듈, 설정 키, 홈 환경변수)
    ("StockLens", ROOT / "mcp", "stock_mcp_server", "stocklens", "STOCKLENS_HOME"),
    ("DartLens", ROOT / "mcp-dart", "dartlens", "dartlens", "DARTLENS_HOME"),
    ("TelegramLens", ROOT / "telegramlens", "telegram_lens", "telegramlens", "TELEGRAMLENS_HOME"),
]

results: list[tuple[str, str, str, str]] = []  # (lens, 시나리오, 판정, 비고)


def record(lens, scenario, ok, note=""):
    results.append((lens, scenario, "PASS" if ok else "FAIL", note))
    print(("  [PASS] " if ok else "  [FAIL] ") + f"{scenario} {note}")


def make_env(home: Path, *, codex: bool, claude_desktop: bool, home_var: str):
    """가짜 PC 하나. Windows 의 Path.home() 은 USERPROFILE 을 본다."""
    appdata = home / "AppData" / "Roaming"
    local = home / "AppData" / "Local"
    (local / "Packages").mkdir(parents=True, exist_ok=True)  # 스토어판 Claude 없음
    appdata.mkdir(parents=True, exist_ok=True)
    if codex:
        (home / ".codex").mkdir(parents=True, exist_ok=True)
    if claude_desktop:
        (appdata / "Claude").mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(
        {
            "USERPROFILE": str(home),
            "HOMEDRIVE": str(home.drive),
            "HOMEPATH": str(home)[len(home.drive):],
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(local),
            home_var: str(home / ".lensdata"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    # 이 두 개가 남아 있으면 실제 홈의 상태가 새어 들어온다.
    for leak in ("STOCKLENS_TARGET", "DARTLENS_TARGET", "TELEGRAMLENS_TARGET"):
        env.pop(leak, None)
    return env


NO_CLAUDE_PREAMBLE = (
    "import shutil;_w=shutil.which;"
    "shutil.which=lambda c,*a,**k:(None if c=='claude' else _w(c,*a,**k));"
)


def run(repo: Path, env: dict, code: str, timeout=180):
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=str(repo), env=env, capture_output=True, timeout=timeout
    )
    return out.returncode, out.stdout.decode("utf-8", "replace"), out.stderr.decode("utf-8", "replace")


def setup_code(module: str, target: str, *, hide_claude: bool) -> str:
    pre = NO_CLAUDE_PREAMBLE if hide_claude else ""
    return (
        pre
        + "import sys;sys.argv=['setup','--target','%s','--json'];" % target
        + "from %s.setup_claude import main;main()" % module
    )


def doctor_code(module: str) -> str:
    return "import sys;sys.argv=['doctor','--json'];from %s.doctor import main;main()" % module


def last_json(text: str):
    """CLI 가 앞에 다른 줄을 찍어도 마지막 JSON 객체만 뽑는다."""
    start = text.find("{")
    while start != -1:
        try:
            return json.loads(text[start:])
        except Exception:
            start = text.find("{", start + 1)
    return None


def check(lens, repo, module, key, home_var, scenario, *, codex, claude_desktop, target,
          expect_targets):
    print(f"\n[{lens}] {scenario}")
    with tempfile.TemporaryDirectory(prefix="leetkit_verify_") as tmp:
        home = Path(tmp)
        env = make_env(home, codex=codex, claude_desktop=claude_desktop, home_var=home_var)

        rc, out, err = run(repo, env, setup_code(module, target, hide_claude=True))
        payload = last_json(out)
        if rc != 0 or payload is None:
            record(lens, scenario, False, f"setup 실패 rc={rc} {err.strip()[:120]}")
            return
        # setup --json 의 targets 모양이 Lens마다 다르다(슬러그 문자열 vs 상세 dict).
        # 어느 쪽이든 같은 사실을 말하게 정규화해서 본다.
        LABEL_SLUG = {
            "Codex CLI": "codex",
            "ChatGPT (Codex)": "codex",
            "Claude Desktop": "claude-desktop",
            "Claude Code CLI": "claude-code",
            "Claude Code": "claude-code",
        }
        got_raw = payload.get("targets") or payload.get("target") or []
        if isinstance(got_raw, str):
            got_raw = [got_raw]
        got = []
        for t in got_raw:
            if isinstance(t, str):
                got.append(t)
            elif isinstance(t, dict):
                got.append(LABEL_SLUG.get(t.get("target_label"), t.get("target_label")))
        if sorted(got) != sorted(expect_targets):
            record(lens, scenario, False, f"등록 대상 {got} (기대 {expect_targets})")
            return

        codex_file = home / ".codex" / "config.toml"
        desktop_file = home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
        if "codex" in expect_targets:
            if not codex_file.exists() or key not in codex_file.read_text(encoding="utf-8"):
                record(lens, scenario, False, "config.toml 에 항목이 안 쓰임")
                return
            if desktop_file.exists():
                record(lens, scenario, False, "없는 Claude 앱 설정 파일을 만들었다")
                return
        if "claude-desktop" in expect_targets and not desktop_file.exists():
            record(lens, scenario, False, "Claude Desktop 설정 파일이 안 쓰임")
            return

        rc2, out2, err2 = run(repo, env, doctor_code(module))
        report = last_json(out2)
        if report is None:
            record(lens, scenario, False, f"doctor JSON 파싱 실패 rc={rc2} {err2.strip()[:120]}")
            return
        dt = report.get("targets") or []
        if sorted(dt) != sorted(expect_targets):
            record(lens, scenario, False, f"doctor targets {dt} (기대 {expect_targets})")
            return

        # Manager 가 그 JSON 을 어떻게 읽는지까지.
        sys.path.insert(0, str(ROOT / "leetkit-manager"))
        from leetkit_manager.models import DoctorReport  # noqa: E402

        parsed = DoctorReport.from_json(report)
        if sorted(parsed.targets) != sorted(expect_targets):
            record(lens, scenario, False, f"Manager 파싱 {parsed.targets}")
            return

        record(lens, scenario, True, f"targets={dt} · 준비상태={parsed.readiness}")


for lens, repo, module, key, home_var in LENSES:
    # 1) Manager 가 실제로 쓰는 길 — 명시적 codex 등록
    check(lens, repo, module, key, home_var, "매니저 경로: --target codex (ChatGPT만 있는 PC)",
          codex=True, claude_desktop=False, target="codex", expect_targets=["codex"])
    # 2) CLI 자동 감지 — ChatGPT만 있는 PC
    check(lens, repo, module, key, home_var, "CLI 자동 감지: ChatGPT만 있는 PC",
          codex=True, claude_desktop=False, target="auto", expect_targets=["codex"])
    # 3) 회귀 확인 — Claude Desktop만 있는 PC는 예전 그대로
    check(lens, repo, module, key, home_var, "회귀: Claude Desktop만 있는 PC",
          codex=False, claude_desktop=True, target="auto", expect_targets=["claude-desktop"])

print("\n" + "=" * 78)
fails = [r for r in results if r[2] == "FAIL"]
for lens, scenario, verdict, note in results:
    print(f"{verdict:5} | {lens:13} | {scenario:42} | {note}")
print("=" * 78)
print(f"{len(results) - len(fails)}/{len(results)} PASS")
sys.exit(1 if fails else 0)

# -*- coding: utf-8 -*-
"""Manager(0.3.0) + Lens(레포 최신 코드) 조합을 실물로 검증.

이 컴퓨터에는 옛 pip 설치본(0.5.0)이 PATH 앞자리를 차지하고 있어서 그냥 돌리면 릴리스
직전 코드가 안 돈다. "어느 실행 파일을 부를지"만 레포 shim 으로 고정하고, 나머지
(등록 · 설정 파일 쓰기 · 진단 · JSON 파싱)는 전부 실물로 돌린다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MANAGER = Path("D:/project/stocklens/leetkit-manager")
REPO = Path("D:/project/stocklens/mcp")
MODULE = "stock_mcp_server"

SHIMS = {
    "stocklens-setup": "from %s.setup_claude import main; main()" % MODULE,
    "stocklens-doctor": "from %s.doctor import main; main()" % MODULE,
    "stocklens-activate": "from %s.licensing import activate_cli; activate_cli()" % MODULE,
}

DRIVER = r'''
import json, os
from pathlib import Path
from unittest.mock import patch
from leetkit_manager import package_service as ps

SHIM_DIR = Path(os.environ["LEETKIT_SHIM_DIR"])


def _resolve(name):
    """"어느 실행 파일인가"만 레포 shim 으로 고정 — 나머지 경로는 전부 실물."""
    cmd = SHIM_DIR / (name + ".cmd")
    return str(cmd) if cmd.exists() else name


# 이 PC 에는 Claude 가 실제로 깔려 있고 떠 있다. "ChatGPT 만 있는 PC" 를 만들려면
# 그 사실만 가려야 한다(설정 경로는 이미 가짜 홈으로 돌려놨다).
with patch.object(ps, "resolve_lens_command", side_effect=_resolve), \
     patch.object(ps, "is_claude_desktop_installed", return_value=False), \
     patch.object(ps, "is_claude_desktop_running", return_value=False), \
     patch.object(ps, "is_claude_code_installed", return_value=False), \
     patch.object(ps, "is_chatgpt_desktop_installed", return_value=True), \
     patch.object(ps, "is_chatgpt_desktop_running", return_value=True):
    from leetkit_manager.ui.api import Api

    api = Api()
    out = {}
    out["resolved_setup_cmd"] = ps.resolve_lens_command("stocklens-setup")
    out["available_targets"] = api.available_targets("stocklens")
    out["default_targets"] = api._default_targets("stocklens")
    out["register"] = api.register("stocklens", ["codex"])
    lens = api.diagnose_one("stocklens", False)
    out["diagnosed_targets"] = lens.get("targets")
    out["readiness"] = lens.get("readiness")
    out["incompatible"] = lens.get("incompatible")
    out["problem_checks"] = [
        c["id"] for c in lens.get("checks", [])
        if c.get("status") not in ("ok", "active", "skip", "info-skip")
    ]
    print("<<<JSON>>>" + json.dumps(out, ensure_ascii=False))
'''


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="leetkit_repo_") as tmp:
        home = Path(tmp)
        appdata = home / "AppData" / "Roaming"
        local = home / "AppData" / "Local"
        (local / "Packages").mkdir(parents=True, exist_ok=True)
        appdata.mkdir(parents=True, exist_ok=True)
        (home / ".codex").mkdir(parents=True, exist_ok=True)

        shim_dir = home / "shims"
        shim_dir.mkdir()
        for name, code in SHIMS.items():
            (shim_dir / (name + ".cmd")).write_text(
                "@echo off\r\n"
                'set "PYTHONPATH=%s"\r\n' % REPO
                + '"%s" -c "%s" %%*\r\n' % (sys.executable, code),
                encoding="utf-8",
            )

        env = dict(os.environ)
        env.update(
            {
                "USERPROFILE": str(home),
                "HOMEDRIVE": home.drive,
                "HOMEPATH": str(home)[len(home.drive):],
                "APPDATA": str(appdata),
                "LOCALAPPDATA": str(local),
                "LEETKIT_SHIM_DIR": str(shim_dir),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            }
        )

        proc = subprocess.run(
            [sys.executable, "-c", DRIVER], cwd=str(MANAGER), env=env,
            capture_output=True, timeout=900,
        )
        text = proc.stdout.decode("utf-8", "replace")
        marker = text.find("<<<JSON>>>")
        if marker == -1:
            print("드라이버 실패 rc=%s" % proc.returncode)
            print(text[-3000:])
            print(proc.stderr.decode("utf-8", "replace")[-3000:])
            return 1
        out = json.loads(text[marker + len("<<<JSON>>>"):])

        codex_file = home / ".codex" / "config.toml"
        desktop_file = appdata / "Claude" / "claude_desktop_config.json"
        checks = []

        def ck(name, ok, note=""):
            checks.append((name, ok, str(note)))

        ck("레포 코드로 돌았는지(shim)", "shims" in (out["resolved_setup_cmd"] or ""),
           out["resolved_setup_cmd"])
        avail = {t["id"]: t["installed"] for t in out["available_targets"]}
        ck("연결 대상: ChatGPT 만 '있음'",
           avail.get("codex") is True and not avail.get("claude-desktop"), avail)
        ck("기본 대상 = codex", out["default_targets"] == ["codex"], out["default_targets"])
        ck("등록 성공", out["register"].get("ok") is True, out["register"].get("error"))
        ck("config.toml 에 실제로 쓰임",
           codex_file.exists() and "stocklens" in codex_file.read_text(encoding="utf-8"), codex_file)
        ck("없는 Claude 설정 파일을 만들지 않음", not desktop_file.exists(), desktop_file)
        ck("진단이 codex 를 알아봄", out["diagnosed_targets"] == ["codex"], out["diagnosed_targets"])
        ck("Lens 버전 호환 문제 없음", not out.get("incompatible"), out.get("readiness"))
        ck("등록 관련 문제 항목 없음",
           not any(str(c).startswith("MCP_CONFIG") for c in out["problem_checks"]),
           "%s · %s" % (out["problem_checks"], out["readiness"]))

        print("\n%-40s %s" % ("항목", "결과"))
        print("-" * 92)
        bad = 0
        for name, ok, note in checks:
            print("%-40s %-5s %s" % (name, "PASS" if ok else "FAIL", note[:46]))
            bad += 0 if ok else 1
        print("-" * 92)
        print("%d/%d PASS" % (len(checks) - bad, len(checks)))
        return 1 if bad else 0


sys.exit(main())

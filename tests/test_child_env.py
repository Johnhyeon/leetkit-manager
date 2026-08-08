"""자식 프로세스에 PyInstaller 부트로더 환경변수를 물려주지 않는다.

물려주면 자식(또 onefile exe인 경우)이 압축을 새로 풀지 않고 **부모의 임시 폴더를
그대로 쓴다**. 그러면 (1) 새 버전이 아니라 옛 코드가 돌고, (2) 부모가 끝나며 그
폴더를 지워 자식이 곧바로 죽는다. 자체 업데이트에서 실제로 이렇게 죽었다:

    FileNotFoundError: Cannot find Microsoft.Web.WebView2.Core.dll

`--wait-for-exit`이 "부모가 죽은 뒤에" 창을 열게 만들어서 100% 재현됐다. exe로
빌드해야만 드러나는 버그라 단위 테스트로 눈에 보이게 잡아둔다 — 여기가 뚫리면
다음 릴리스에서 같은 방식으로 조용히 재발한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from leetkit_manager import package_service, process_runner

BOOTLOADER_VARS = {
    "_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI570002",
    "_PYI_ARCHIVE_FILE": r"C:\app\LeetKitManager.exe",
    "_PYI_PARENT_PROCESS_LEVEL": "1",
    "_MEIPASS2": r"C:\Temp\_MEI570002",
}


@pytest.fixture
def _frozen_env(monkeypatch):
    """onefile exe로 실행 중인 것처럼 부트로더 변수를 심어둔다."""
    for key, value in BOOTLOADER_VARS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("KEEP_ME", "yes")


class TestChildEnv:
    def test_strips_bootloader_vars(self, _frozen_env):
        env = process_runner.child_env()
        for key in BOOTLOADER_VARS:
            assert key not in env, f"{key}를 그대로 물려주면 자식이 부모 폴더를 쓴다"

    def test_keeps_everything_else(self, _frozen_env):
        assert process_runner.child_env()["KEEP_ME"] == "yes"

    def test_does_not_mutate_our_own_environment(self, _frozen_env):
        process_runner.child_env()
        # 우리 프로세스의 _MEIPASS는 살아 있어야 한다 — 지우면 우리가 번들을 못 찾는다.
        assert os.environ["_PYI_APPLICATION_HOME_DIR"] == BOOTLOADER_VARS["_PYI_APPLICATION_HOME_DIR"]


class TestRelaunchPassesCleanEnv:
    """자체 업데이트가 새 프로세스를 띄우는 두 경로 — 여기가 이 버그의 현장이었다."""

    def test_replace_running_exe(self, _frozen_env, tmp_path, monkeypatch):
        current = tmp_path / "LeetKitManager.exe"
        current.write_text("old", encoding="utf-8")
        new_exe = tmp_path / "new.exe"
        new_exe.write_text("new", encoding="utf-8")
        monkeypatch.setattr(package_service.sys, "executable", str(current))

        with patch.object(package_service.subprocess, "Popen") as popen:
            package_service.replace_running_exe(new_exe)

        env = popen.call_args.kwargs["env"]
        for key in BOOTLOADER_VARS:
            assert key not in env

    def test_relaunch_after_exit(self, _frozen_env):
        with patch.object(package_service.subprocess, "Popen") as popen:
            assert package_service.relaunch_after_exit() is True

        env = popen.call_args.kwargs["env"]
        for key in BOOTLOADER_VARS:
            assert key not in env


class TestLensCallsPassCleanEnv:
    """Lens CLI 호출도 같은 환경을 물려준다 — Lens가 언젠가 exe로 포장돼도 안전하게."""

    def test_run_cli(self, _frozen_env):
        with patch.object(process_runner.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            process_runner.run_cli(["whatever"])

        env = run.call_args.kwargs["env"]
        for key in BOOTLOADER_VARS:
            assert key not in env


def test_every_spawn_site_passes_env():
    """새로 추가되는 Popen이 env를 빠뜨리지 않게 — 이 버그가 조용히 재발하는 유일한 길이다."""
    import re

    for name in ("package_service.py", "process_runner.py", "interactive_process.py"):
        source = (Path(package_service.__file__).parent / name).read_text(encoding="utf-8")
        for match in re.finditer(r"subprocess\.(Popen|run)\(", source):
            # 여는 괄호부터 균형이 맞는 닫는 괄호까지 잘라낸다
            start = match.end() - 1
            depth, end = 0, start
            for i in range(start, len(source)):
                if source[i] == "(":
                    depth += 1
                elif source[i] == ")":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            call = source[start:end]
            assert "env=" in call, f"{name}의 subprocess 호출에 env=child_env()가 없다:\n{call[:200]}"

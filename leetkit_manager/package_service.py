"""uv 기반 설치·버전 조회·업데이트·롤백.

업데이트/롤백 모두 같은 명령 형태를 쓴다 — `uv tool install --force <package>==<version>`.
`upgrade`가 아니라 목표 버전을 명시해서 동일 버전 재설치와 구버전 롤백을 하나의 함수로
처리한다(LeetKit Manager Program Requirements 3.4).

설치 여부·현재 버전의 1차 출처는 각 Lens의 `doctor --json`(installed_version 필드)이다 —
여기 `list_installed_tools()`는 doctor 호출 자체가 아직 불가능한 최초 설치 흐름에서만
보조적으로 쓰는 fallback이다.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from leetkit_manager.process_runner import ProcessResult, run_cli

_PYPI_TIMEOUT = 10.0
_INSTALL_TIMEOUT = 120.0  # PyPI 다운로드가 걸리므로 doctor(30초)보다 넉넉하게.


def _uv_tool_bin_dirs() -> list[Path]:
    """uv tool install이 실행 스크립트를 두는 표준 위치들 — 각 Lens의 setup_claude.py가
    자기 자신의 MCP entry 경로를 정할 때 쓰는 것과 동일한 탐색 순서."""
    candidates: list[Path] = []
    env = os.environ.get("UV_TOOL_BIN_DIR")
    if env:
        candidates.append(Path(env))
    xdg = os.environ.get("XDG_BIN_HOME")
    if xdg:
        candidates.append(Path(xdg))
    candidates.append(Path.home() / ".local" / "bin")
    return [p for p in candidates if p.exists()]


def resolve_lens_command(command_name: str) -> str:
    """`command_name`의 실행 경로를 uv tool bin 디렉터리에서 먼저 찾는다.

    실사용 중 발견된 문제: 옛 `pip install <lens>-mcp` 잔재가 시스템 Python의 Scripts/에
    남아 있으면, PATH 검색 순서상 그게 uv가 최신 버전으로 관리하는 실행 파일보다 먼저
    잡힐 수 있다 — Manager가 최신으로 업데이트했다고 믿는데 실제로는 구버전(옛 JSON
    계약도 모르는)을 호출하게 된다. 이 함수는 uv의 표준 위치를 먼저 확인해 그 문제를
    피한다. 못 찾으면 bare 이름을 그대로 반환해(PATH 조회는 process_runner가 담당)
    uv 없이 pip로만 설치한 환경도 그대로 동작한다.
    """
    for bin_dir in _uv_tool_bin_dirs():
        for name in (f"{command_name}.exe", command_name):
            candidate = bin_dir / name
            if candidate.exists():
                return str(candidate)
    return command_name


def latest_pypi_version(package_name: str) -> str | None:
    """PyPI JSON API에서 최신 버전 조회. 실패해도 예외를 던지지 않고 None."""
    try:
        resp = httpx.get(f"https://pypi.org/pypi/{package_name}/json", timeout=_PYPI_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("info", {}).get("version") or None
    except Exception:
        return None


def install_version(package_name: str, version: str, *, timeout: float = _INSTALL_TIMEOUT) -> ProcessResult:
    """`uv tool install --force <package>==<version>` 실행. 업데이트·롤백 공용 진입점."""
    return run_cli(
        ["uv", "tool", "install", "--force", f"{package_name}=={version}"],
        timeout=timeout,
    )


def version_gt(latest: str, current: str) -> bool:
    """semver 비교. 실패 시 단순 문자열 비교 fallback(각 Lens의 update-check 로직과 동일)."""
    try:
        from packaging.version import Version

        return Version(latest) > Version(current)
    except Exception:
        return bool(latest) and latest != current


def list_installed_tools() -> dict[str, str]:
    """`uv tool list` 결과를 {package_name: version}으로. 실패하면 빈 딕셔너리."""
    result = run_cli(["uv", "tool", "list"], timeout=15.0)
    if not result.ok:
        return {}
    installed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("v"):
            installed[parts[0]] = parts[1].lstrip("v")
    return installed

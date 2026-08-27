"""uv 기반 설치·버전 조회·업데이트·롤백.

업데이트/롤백 모두 같은 명령 형태를 쓴다 — `uv tool install --force <package>==<version>`.
`upgrade`가 아니라 목표 버전을 명시해서 동일 버전 재설치와 구버전 롤백을 하나의 함수로
처리한다(LeetKit Manager Program Requirements 3.4).

설치 여부·현재 버전의 1차 출처는 각 Lens의 `doctor --json`(installed_version 필드)이다 —
여기 `list_installed_tools()`는 doctor 호출 자체가 아직 불가능한 최초 설치 흐름에서만
보조적으로 쓰는 fallback이다.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

from leetkit_manager.process_runner import ProcessResult, child_env, run_cli, run_cli_streaming

_PYPI_TIMEOUT = 10.0
# PyPI 다운로드가 걸리므로 doctor(30초)보다 넉넉하게. 120초는 실제로 부족했다 —
# TelegramLens는 telethon·Pillow·pystray까지 받아야 해서 느린 회선이나 가상머신에서
# 중간에 잘렸고, 화면에는 이유 없이 "실패했습니다"만 떴다. 오래 걸리는 건 진행률
# 오버레이가 보여주므로, 넉넉히 두고 정말 멈춘 경우만 끊는 편이 낫다.
_INSTALL_TIMEOUT = 420.0
_UV_INSTALL_TIMEOUT = 180.0
_GITHUB_RELEASE_TIMEOUT = 10.0
_EXE_DOWNLOAD_TIMEOUT = 120.0
_EXE_SHA256_ASSET = "LeetKitManager.exe.sha256"
_GITHUB_RELEASES_LATEST_URL = "https://api.github.com/repos/Johnhyeon/leetkit-manager/releases/latest"


def _uv_tool_site_packages(package_name: str) -> list[Path]:
    """uv tool 환경(uv/tools/<패키지>/) 안의 site-packages 후보."""
    import glob as _glob

    roots: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    bases = []
    if local:
        bases.append(Path(local) / "uv" / "tools" / package_name)
    bases.append(Path.home() / ".local" / "share" / "uv" / "tools" / package_name)
    for base in bases:
        if not base.exists():
            continue
        for pat in ("Lib/site-packages", "lib/python*/site-packages"):
            roots += [Path(x) for x in _glob.glob(str(base / pat))]
    return [r for r in roots if r.is_dir()]


def cleanup_stale_dist_metadata(
    package_name: str,
    installed_version: str,
    *,
    site_packages: list[Path] | None = None,
) -> dict:
    """설치 성공 직후, 같은 패키지의 잔존 배포 메타를 정리한다(TL-01 요구 4).

    실측(UAT): 전역 환경에 "~elegramlens_mcp-*.dist-info"(pip 임시 리네임이
    깨진 잔재)와 옛 버전 dist-info 가 새 설치 옆에 남아, importlib.metadata 가
    과거 버전을 보고했다. 지우는 것은 **메타데이터 폴더뿐**이고, 방금 설치한
    버전의 메타는 절대 건드리지 않는다. 실패는 삼킨다 - 정리가 안 됐다고
    설치를 실패로 만들지 않는다.
    """
    normalized = package_name.replace("-", "_").lower()
    broken_stem = "~" + normalized[1:]
    removed: list[str] = []
    kept: list[str] = []
    errors: list[str] = []
    for root in (site_packages if site_packages is not None
                 else _uv_tool_site_packages(package_name)):
        root = Path(root)
        if not root.is_dir():
            continue
        for entry in list(root.iterdir()):
            name = entry.name
            low = name.lower()
            target = False
            if low.startswith(broken_stem):
                target = True                      # pip 임시 리네임 잔재
            elif low.endswith(".dist-info"):
                stem = low[: -len(".dist-info")]
                pkg, _, ver = stem.rpartition("-")
                if pkg == normalized:
                    if ver == installed_version:
                        kept.append(str(entry))
                        continue
                    target = True                  # 같은 패키지의 옛 메타
            if not target:
                continue
            try:
                import shutil as _shutil

                _shutil.rmtree(entry)
                removed.append(str(entry))
            except OSError as e:
                errors.append(f"{entry}: {type(e).__name__}")
    return {"removed": removed, "kept": kept, "errors": errors}


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


def _version_sort_key(value: str):
    """버전 정렬용 키. packaging이 있으면 그걸 쓰고, 없으면 숫자 튜플로."""
    try:
        from packaging.version import Version

        return (1, Version(value))
    except Exception:
        parts = _version_tuple(value)
        # 못 읽는 버전은 항상 뒤로 밀어 최신으로 뽑히지 않게 한다.
        return (0, parts) if parts else (-1, ())


def _simple_index_versions(package_name: str, *, timeout: float) -> list[str]:
    """simple 인덱스가 실제로 제공하는 버전 목록. 못 읽으면 빈 목록."""
    try:
        resp = httpx.get(
            f"https://pypi.org/simple/{package_name}/",
            timeout=timeout,
            headers={"Accept": "application/vnd.pypi.simple.v1+json"},
            follow_redirects=True,
        )
        resp.raise_for_status()
        versions = resp.json().get("versions")
    except Exception:
        return []
    return [v for v in versions if isinstance(v, str)] if isinstance(versions, list) else []


def latest_pypi_version(package_name: str, *, timeout: float = _PYPI_TIMEOUT) -> str | None:
    """설치 가능한 최신 버전. 실패해도 예외를 던지지 않고 None.

    **uv가 실제로 보는 곳(simple 인덱스)을 본다.** 예전엔 JSON API를 봤는데, 그쪽이
    simple 인덱스보다 먼저 갱신된다 — 새 버전을 올린 직후 몇 분간 Manager는 "최신은
    0.4.15"라고 판단하는데 uv는 그 버전을 못 찾아 `uv tool install pkg==0.4.15`가
    실패했다. 화면에는 이유 없이 "실패했습니다"만 떴고, 시간이 지나 인덱스가 따라잡으면
    저절로 되니 원인을 짚기도 어려웠다(실제로 오늘 세 번 겪었다).

    같은 곳을 보면 그 어긋남이 원천적으로 안 생긴다 — 우리가 "최신"이라고 말한 버전은
    uv가 반드시 설치할 수 있는 버전이다.
    """
    versions = _simple_index_versions(package_name, timeout=timeout)
    if versions:
        return max(versions, key=_version_sort_key)
    # simple 인덱스를 못 읽으면 예전 경로로라도 알려준다(아예 모르는 것보단 낫다).
    try:
        resp = httpx.get(f"https://pypi.org/pypi/{package_name}/json", timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("info", {}).get("version") or None
    except Exception:
        return None


def _uv_install_dirs() -> list[Path]:
    """uv 공식 설치 스크립트가 uv 자신을 두는 위치들. uv tool bin dir과 대체로 겹치지만
    옛 버전은 `~/.cargo/bin`에 두었어서 둘 다 본다."""
    return _uv_tool_bin_dirs() + [Path.home() / ".local" / "bin", Path.home() / ".cargo" / "bin"]


def resolve_uv_command() -> str:
    """`uv` 실행 파일의 실제 경로. 못 찾으면 bare 이름("uv")을 그대로 반환한다.

    실사용 중 재현해서 확인한 문제: `ensure_uv_available()`이 astral 설치 스크립트로
    uv를 방금 깔면 uv는 `~/.local/bin`에 생기지만, 그 설치 스크립트는 *영구* PATH
    (레지스트리)만 갱신한다 — 이미 실행 중인 이 프로세스의 PATH에는 반영되지 않는다.
    그래서 곧바로 bare `"uv"`로 호출하면 not_found로 실패했다. `is_uv_available()`이
    bin dir 스캔으로 True를 돌려주는 바람에 다음 실행부터는 부트스트랩마저 건너뛰어,
    사용자가 재부팅(또는 새 셸)하기 전까지 Lens 설치가 계속 실패했다 —
    "exe 하나만 받으면 끝"이라는 신규 구매자 시나리오가 통째로 깨지는 경로였다.
    resolve_lens_command()가 Lens 실행 파일에 대해 하는 것과 같은 해결을 uv에도 한다.
    """
    found = shutil.which("uv")
    if found and Path(found).is_absolute():
        return found
    for bin_dir in _uv_install_dirs():
        for name in ("uv.exe", "uv"):
            candidate = bin_dir / name
            if candidate.exists():
                return str(candidate)
    return "uv"


def is_uv_available() -> bool:
    """uv 실행 파일 자체(Lens 실행 스크립트가 아니라 uv 명령 그 자신)가 이 컴퓨터에
    있는지. 단일 exe만 받은 사용자는 uv/Python을 따로 설치한 적이 없을 수 있어서 —
    이 경우 install_version()이 그냥 조용히 "not_found"로 실패하고 있었다(실제
    지적된 문제: exe는 열리는데 Lens 설치는 항상 실패)."""
    if shutil.which("uv"):
        return True
    for bin_dir in _uv_install_dirs():
        for name in ("uv.exe", "uv"):
            if (bin_dir / name).exists():
                return True
    return False


def ensure_uv_available(*, timeout: float = _UV_INSTALL_TIMEOUT) -> ProcessResult:
    """uv가 없으면 공식 설치 스크립트로 조용히 설치한다. 이미 있으면 바로 성공 반환.

    uv 공식 설치 스크립트(astral.sh)를 그대로 쓴다 — uv 자신을 우리가 재구현/번들링
    하지 않고, Lens들의 doctor.py가 "uv not found"일 때 이미 안내하던 것과 동일한
    명령을 대신 실행해주는 것뿐이다.
    """
    if is_uv_available():
        return ProcessResult(cmd=["uv", "--version"], exit_code=0, stdout="", stderr="", timed_out=False, duration_s=0.0)

    if sys.platform == "win32":
        cmd = ["powershell", "-ExecutionPolicy", "ByPass", "-Command", "irm https://astral.sh/uv/install.ps1 | iex"]
    else:
        cmd = ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"]
    return run_cli(cmd, timeout=timeout)


def install_version(package_name: str, version: str, *, timeout: float = _INSTALL_TIMEOUT) -> ProcessResult:
    """`uv tool install --force <package>==<version>` 실행. 업데이트·롤백 공용 진입점.

    uv 자체가 없으면(단일 exe만 받은 신규 구매자) 먼저 조용히 설치한 뒤 계속 진행한다.
    """
    if not is_uv_available():
        _set_install_progress("설치 도구(uv)를 준비하는 중…")
        ensure_result = ensure_uv_available()
        if not ensure_result.ok:
            return ensure_result  # uv 설치 자체가 실패 — 원인이 그대로 보이게 전달

    _set_install_progress("패키지를 내려받는 중…")
    result = run_cli_streaming(
        [resolve_uv_command(), "tool", "install", "--force", f"{package_name}=={version}"],
        timeout=timeout,
        on_line=lambda line: _set_install_progress(_humanize_uv_line(line)),
    )
    _set_install_progress(None)
    if result.ok:
        # 설치가 성공했을 때만 잔존 메타를 정리한다(TL-01). 실패한 설치 뒤에
        # 지우면 어떤 메타가 진짜인지 알 수 없다.
        try:
            cleanup_stale_dist_metadata(package_name, version)
        except Exception:
            pass
    return result


# 설치는 수십 초 걸린다(pandas·numpy까지 받는다). 그동안 화면에 아무 변화가 없으면
# 사용자는 멈춘 줄 안다 — uv가 흘리는 진행 줄을 사람이 읽을 말로 바꿔 여기 담아두고,
# UI가 짧은 주기로 읽어간다. GIL 아래 문자열 대입/읽기라 별도 락은 두지 않는다
# (한 번에 설치 하나만 돌아간다는 전제는 UI가 카드 단위 잠금으로 이미 보장한다).
_install_progress: str | None = None


def _set_install_progress(text: str | None) -> None:
    global _install_progress
    if text is not None and not text.strip():
        return  # 빈 줄로 기존 표시를 지우지 않는다
    _install_progress = text


def current_install_progress() -> str | None:
    return _install_progress


def _humanize_uv_line(line: str) -> str | None:
    """uv의 영어 진행 출력을 고객이 읽을 한국어로. 알아볼 수 없는 줄은 무시해서
    (None) 직전 표시를 유지한다 — 내부 로그를 그대로 노출하지 않는다."""
    s = line.strip()
    if not s:
        return None
    low = s.lower()
    if low.startswith("downloading"):
        return f"내려받는 중… ({s.split(' ', 1)[1]})" if " " in s else "내려받는 중…"
    if low.startswith("downloaded"):
        return "내려받기 완료 — 준비하는 중…"
    if low.startswith("resolved"):
        return "필요한 구성요소를 확인했습니다…"
    if low.startswith("prepared"):
        return "설치 준비 완료…"
    if low.startswith("installed"):
        return "설치를 마무리하는 중…"
    return None


def processes_using_package(package_name: str, command_names: list[str] | None = None) -> list:
    """이 Lens의 파일을 붙잡고 있어 설치·삭제를 막는 프로세스들.

    Claude Desktop만 닫으면 되는 줄 알았는데 실제로는 아니었다 — TelegramLens는 트레이
    아이콘·수집 데몬·MCP 서버가 Claude와 무관하게 따로 살아 있고, 이들이 uv 도구 폴더의
    python.exe를 쥐고 있어서 Claude를 껐는데도 계속 "액세스가 거부되었습니다"가 났다
    (직접 재현해서 확인). 그래서 Claude가 아니라 *그 패키지에서 실행 중인 것 전부*를
    대상으로 잡는다.

    두 종류를 찾는다:
      1) uv 도구 폴더(`...\\uv\\tools\\<패키지>\\`) 안의 실행 파일로 돌고 있는 프로세스
      2) 그 Lens의 명령 이름으로 된 실행 파일(트레이·데몬 등) — 살려두면 1)을 다시 띄운다
    """
    try:
        import psutil
    except Exception:
        return []

    tool_marker = f"/uv/tools/{package_name}/".lower()  # 구분자는 /로 통일해 비교
    shim_names = {f"{name}.exe".lower() for name in (command_names or [])}
    me = os.getpid()

    found = []
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                if proc.pid == me:
                    continue
                exe = _normalize_exe_path(proc.info.get("exe") or "")
                if exe and tool_marker in exe:
                    found.append(proc)
                elif shim_names and (proc.info.get("name") or "").lower() in shim_names:
                    found.append(proc)
            except Exception:
                continue
    except Exception:
        return []
    return found


def stop_processes_using_package(
    package_name: str, command_names: list[str] | None = None, *, timeout: float = 8.0
) -> int:
    """위에서 찾은 프로세스들을 정리한다. 반환: 실제로 종료시킨 개수.

    전부 우리 제품이 띄운 보조 프로세스(트레이·데몬·MCP 서버)라 멈춰도 데이터가 상하지
    않는다 — 트레이는 사용자가 다시 켜면 되고, MCP 서버·데몬은 Claude를 열면 다시 뜬다.
    """
    procs = processes_using_package(package_name, command_names)
    if not procs:
        return 0

    import psutil

    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            pass
    _gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except Exception:
            pass
    psutil.wait_procs(alive, timeout=3)
    return len(procs)


def looks_like_file_in_use(result: ProcessResult) -> bool:
    """설치·삭제 실패가 "파일을 누가 쓰고 있어서"인지.

    Claude Desktop은 각 Lens를 MCP 서버로 띄우는데, 그 프로세스가 uv 도구 폴더의
    파일을 잡고 있으면 `uv tool install/uninstall`이 접근 거부로 실패한다. 더 나쁜 건
    이때 패키지 폴더는 지워졌는데 `~/.local/bin`의 실행 파일 껍데기는 남는 경우가
    있다는 것 — 그 상태가 곧 "호환되지 않는 Lens 버전"(ModuleNotFoundError)이다.
    실사용에서 이 두 증상이 같은 원인으로 확인됐다.

    Windows 표시 언어에 따라 메시지가 한국어/영어로 갈리므로 둘 다 본다.
    """
    text = f"{result.stdout}\n{result.stderr}".lower()
    markers = (
        "os error 5",
        "액세스가 거부",
        "access is denied",
        "permission denied",
        "being used by another process",
        "다른 프로세스가 사용",
    )
    return any(m in text for m in markers)


def _legacy_shadow_command(command_name: str) -> Path | None:
    """PATH에서 command_name이 실제로 어디로 잡히는지 확인해, uv tool bin 디렉터리
    밖에 있으면(=uv가 관리하지 않는 옛 pip 설치 잔재) 그 경로를 반환한다.
    resolve_lens_command와 반대 방향 확인 — 저건 "uv 걸 우선 찾기", 이건 "uv 밖에
    남은 걸 찾기"."""
    found = shutil.which(command_name)
    if not found:
        return None
    # Windows의 shutil.which()는 PATH뿐 아니라 *현재 작업 디렉터리*도 먼저 뒤지고,
    # 거기서 맞으면 상대경로(".\\name.EXE")를 돌려준다(실제로 확인함). exe를 더블클릭하면
    # 작업 디렉터리가 그 exe가 있는 폴더가 되므로 — 다운로드 폴더처럼 남이 파일을 놓을 수
    # 있는 위치에 가짜 `<lens>-doctor.exe`와 `python.exe`를 심어두면, 아래 uninstall
    # 경로가 그 python.exe를 실제로 실행하게 된다. PATH에서 제대로 찾은 결과는 항상
    # 절대경로이므로, 상대경로면 작업 디렉터리에서 걸린 것으로 보고 무시한다.
    if not Path(found).is_absolute():
        return None
    found_path = Path(found).resolve()
    uv_dirs = {d.resolve() for d in _uv_tool_bin_dirs()}
    if found_path.parent in uv_dirs:
        return None
    return found_path


def find_legacy_pip_shadow(command_names: list[str]) -> Path | None:
    """이 Lens의 명령어들(doctor/setup/activate) 중 하나라도 uv 관리 밖에서 PATH에
    잡히면 그 실행 파일 경로를 반환한다 — 옛 `pip install`이 여전히 남아 uv가 새로
    설치한 버전을 가려버리고 있다는 신호(실사용 중 발견: "업데이트"를 눌러도 계속
    "호환되지 않는 버전"으로 남던 원인)."""
    for name in command_names:
        shadow = _legacy_shadow_command(name)
        if shadow is not None:
            return shadow
    return None


def _python_from_shebang(script_path: Path) -> Path | None:
    """pip 콘솔 스크립트의 shebang 에서 인터프리터 경로를 읽는다.

    맥·리눅스에서 이게 가장 정확하다 — 스크립트가 텍스트 파일이고 첫 줄이
    `#!/path/to/python3` 이라 어느 Python 이 이걸 깔았는지 스스로 적어둔다.
    특히 `--user` 설치(`~/Library/Python/3.x/bin/`)는 그 폴더에 python 이 같이 있지
    않아서 옆자리 추정으로는 못 찾는다. Windows 의 .exe 는 이진 파일이라 그냥 통과한다."""
    try:
        with open(script_path, "rb") as f:
            first = f.readline(1024)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    line = first[2:].decode("utf-8", "replace").strip().strip('"').strip("'")
    parts = line.split()
    if not parts:
        return None
    # `#!/usr/bin/env python3` 형태면 뒤엣것이 인터프리터 이름이다 — 그건 경로가 아니라
    # PATH 조회 대상이라 여기서는 쓰지 않는다(엉뚱한 Python 을 고르느니 포기한다).
    if Path(parts[0]).name == "env":
        return None
    candidate = Path(parts[0])
    return candidate if candidate.is_absolute() and candidate.exists() else None


def _infer_python_for_script(script_path: Path) -> Path | None:
    """pip entry-point 스크립트 경로에서 그걸 설치한 python 실행 파일을 추정한다.

    Windows 표준 설치는 Scripts/의 부모 폴더에 python.exe 가 있고 venv 는 Scripts/ 안에
    같이 있다. 맥·리눅스는 같은 `bin/` 안에 python3 가 있거나, 없으면 shebang 이 답이다."""
    from_shebang = _python_from_shebang(script_path)
    if from_shebang is not None:
        return from_shebang

    # 어떤 이름을 찾을지는 **지금 돌고 있는 OS** 가 아니라 그 설치본의 레이아웃이 정한다.
    # (지원 번들·원격 진단처럼 다른 OS 에서 만들어진 경로를 다룰 수도 있다.)
    names = ("python.exe", "python3", "python")
    parents = (script_path.parent, script_path.parent.parent, script_path.parent.parent / "bin")
    for parent in parents:
        for name in names:
            candidate = parent / name
            if candidate.exists():
                return candidate
    return None


def _site_packages_for(python_exe: Path) -> list[Path]:
    """그 Python 의 site-packages 후보.

    Windows 는 `<prefix>\\Lib\\site-packages`, 맥·리눅스는
    `<prefix>/lib/pythonX.Y/site-packages` 로 레이아웃이 다르다. Windows 것만 보면
    맥에서는 editable 판정이 **항상 False** 가 되어 개발용 설치를 보호하지 못한다."""
    base = python_exe.parent
    candidates = [base / "Lib" / "site-packages", base.parent / "Lib" / "site-packages"]
    for root in (base.parent, base.parent.parent):
        lib = root / "lib"
        try:
            if lib.is_dir():
                candidates.extend(sorted(lib.glob("python*/site-packages")))
        except OSError:
            continue
    seen: set[Path] = set()
    out: list[Path] = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.is_dir():
            out.append(path)
    return out


def is_editable_install(python_exe: Path, package_name: str) -> bool:
    """그 Python 환경에서 이 패키지가 개발용(editable) 설치인지.

    `pip install -e` 로 깔린 것도 PATH 에서 uv 관리 밖으로 잡히므로 "옛 pip 잔재"와
    생김새가 같다. 고객 PC 에는 없지만 개발 PC 에는 있고, 자동으로 지우면 작업 환경이
    통째로 사라진다.

    판정은 파일로 한다. `pip show` 출력을 읽는 방법을 먼저 썼는데, 실기기에서 pip 자신의
    로깅 오류로 출력이 중간에 잘려서 editable 인데 아니라고 답했다. 표준(PEP 610)인
    `dist-info/direct_url.json` 의 `dir_info.editable` 이 확실하다. 옛 방식(develop)이나
    일부 백엔드는 `.pth` 흔적만 남기므로 그것도 같이 본다.

    확인 자체가 안 되면 False — 판단 못 하는 걸 editable 로 단정해 정리를 막지는 않는다."""
    norm = package_name.replace("-", "_").lower()
    for site_packages in _site_packages_for(python_exe):
        try:
            for dist_info in site_packages.glob(f"{norm}-*.dist-info"):
                try:
                    data = json.loads((dist_info / "direct_url.json").read_text(encoding="utf-8"))
                except Exception:
                    continue
                if (data.get("dir_info") or {}).get("editable"):
                    return True
            for pattern in (f"*editable*{norm}*.pth", f"*{norm}*editable*.pth"):
                if any(site_packages.glob(pattern)):
                    return True
        except OSError:
            continue
    return False


def uninstall_legacy_pip_shadow(package_name: str, shadow_path: Path, *, timeout: float = _INSTALL_TIMEOUT) -> ProcessResult:
    """옛 pip 설치 잔재를 그 실행 파일과 같이 있는 python.exe로 pip uninstall한다.
    같이 있는 python.exe를 못 찾으면(레이아웃을 못 알아본 경우) 엉뚱한 Python
    환경에서 잘못 실행하는 대신 안전하게 실패로 반환한다.

    개발용(editable) 설치는 건드리지 않는다 — 생김새가 같아서 구분이 안 되는데, 지우면
    작업 환경이 사라진다."""
    python_exe = _infer_python_for_script(shadow_path)
    if python_exe is None:
        return ProcessResult(
            cmd=["pip", "uninstall", package_name],
            exit_code=1, timed_out=False, duration_s=0.0, stdout="",
            stderr=f"'{shadow_path}' 를 설치한 파이썬을 찾을 수 없어 자동으로 지울 수 없습니다. 수동으로 삭제해주세요.",
        )
    if is_editable_install(python_exe, package_name):
        return ProcessResult(
            cmd=["pip", "uninstall", package_name],
            exit_code=1, timed_out=False, duration_s=0.0, stdout="",
            stderr=f"'{shadow_path}' 는 개발용으로 연결된 설치라 자동으로 지우지 않았습니다.",
        )
    return run_cli([str(python_exe), "-m", "pip", "uninstall", package_name, "-y"], timeout=timeout)


def uninstall_version(package_name: str, *, timeout: float = _INSTALL_TIMEOUT) -> ProcessResult:
    """`uv tool uninstall <package>` 실행 — 완전 재설치가 필요한 경우(예: PATH에
    uv 관리 밖의 낡은 실행 파일이 남아 "호환되지 않는 버전"으로 계속 잡히는 문제)를
    위한 진입점. uv가 아예 없으면 애초에 uv로 설치된 것도 없다는 뜻이라 조용히
    성공 취급한다(설치 자체와 달리, 없는 걸 지우려는 시도를 실패로 볼 이유가 없다).

    uv가 아닌 다른 방식(pip 등)으로 설치된 실행 파일은 이 명령이 못 지운다 — 그 경우는
    Manager가 감지·제거할 수 없는 범위 밖이라 사용자가 직접 지워야 한다."""
    if not is_uv_available():
        return ProcessResult(cmd=["uv", "tool", "uninstall", package_name], exit_code=0, stdout="", stderr="", timed_out=False, duration_s=0.0)
    return run_cli([resolve_uv_command(), "tool", "uninstall", package_name], timeout=timeout)


def _version_tuple(value: str) -> tuple[int, ...] | None:
    """"0.1.6" → (0, 1, 6). 숫자로 안 읽히는 부분이 있으면 None."""
    parts = []
    for chunk in (value or "").strip().split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            return None
        parts.append(int(digits))
    return tuple(parts) or None


def version_gt(latest: str, current: str) -> bool:
    """`latest`가 `current`보다 정말로 새 버전인가.

    폴백이 예전엔 `latest != current`였다 — "다르면 새 버전"이라는 뜻이라, 최신을 쓰는
    사람에게 **옛 버전으로 내려가라고** 권했다. 실제로 0.1.6을 쓰는데 "0.1.5로
    업데이트하세요"가 떴다(PyPI 인덱스 반영이 잠깐 늦어 latest가 0.1.5로 읽힌 순간).

    폴백을 탄 이유는 packaging이 의존성에 없어서였다. 그건 따로 선언해서 고쳤지만,
    없을 때도 안전해야 하므로 숫자 비교로 바꾼다. 그것마저 안 되면 False다 —
    잘못된 업데이트 안내보다 안내를 안 하는 쪽이 낫다."""
    if not latest:
        return False
    try:
        from packaging.version import Version

        return Version(latest) > Version(current)
    except Exception:
        pass
    latest_parts, current_parts = _version_tuple(latest), _version_tuple(current)
    if latest_parts is None or current_parts is None:
        return False
    return latest_parts > current_parts


# Claude Code CLI가 놓이는 위치들 — 여기 있는 `claude.exe`는 무슨 일이 있어도
# Claude Desktop으로 보면 안 된다(종료 기능이 사용자의 CLI 작업을 죽인다).
# 아래 마커들은 모두 "구분자를 /로 통일하고 소문자로 낮춘" 경로와 비교한다
# (_normalize_exe_path). Windows/macOS 표기 차이를 여기서 한 번에 흡수한다.
_CLAUDE_CLI_PATH_MARKERS = (
    "/.local/bin/",
    "/node_modules/",
    "/npm/",
    "/.bun/",
    "/appdata/roaming/npm/",
    "/usr/local/bin/",   # macOS Homebrew·수동 설치 CLI
    "/homebrew/",
)

# Claude Desktop 설치 위치 — 버전·퍼블리셔 해시·드라이브가 사용자마다 달라서
# 전체 경로가 아니라 '변하지 않는 조각'으로만 맞춘다.
#   MSIX(스토어):  <드라이브>:\Program Files\WindowsApps\Claude_<버전>_x64__<퍼블리셔>\app\claude.exe
#   고전 인스톨러: %LOCALAPPDATA%\AnthropicClaude\app-<버전>\claude.exe
_CLAUDE_DESKTOP_PATH_MARKERS = (
    "/windowsapps/claude_",   # Windows MSIX(스토어)
    "/anthropicclaude/",      # Windows 고전 인스톨러
    "/claude.app/",           # macOS — /Applications/Claude.app/Contents/MacOS/Claude
)


def _normalize_exe_path(exe_path: str) -> str:
    """경로 비교용 정규화 — 구분자를 /로 통일하고 소문자로. Windows는 \\와 / 둘 다
    쓰이고 대소문자를 안 가리며, macOS는 /만 쓴다. 한쪽 표기만 가정하면 다른 OS에서
    조용히 안 잡힌다(실제로 macOS 경로가 안 잡히는 걸 확인했다)."""
    return exe_path.replace("\\", "/").lower()


def _process_has_visible_window(pid: int) -> bool:
    """이 PID가 보이는 창을 갖고 있는지. Claude Desktop은 GUI 앱이고 CLI는 아니므로,
    아는 설치 경로 목록에 없는 새 위치에 설치되더라도 이걸로 구분할 수 있다."""
    if sys.platform != "win32":
        return False
    try:
        import win32gui
        import win32process
    except Exception:
        return False

    found = []

    def _enum(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            if not win32gui.GetWindowText(hwnd):
                return
            _tid, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if wpid == pid:
                found.append(hwnd)
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        return False
    return bool(found)


def _is_claude_desktop_exe(exe_path: str | None, *, pid: int | None = None) -> bool:
    """실행 파일 경로가 Claude *Desktop 앱*인지.

    이름만으로는 절대 판별하면 안 된다 — 실기기에서 확인한 바로 `claude.exe`는 두 종류가
    동시에 떠 있다(Claude Desktop / Claude Code CLI). 이름만 보면 CLI만 쓰는 사용자에게
    엉뚱한 재시작 안내가 나가고, 종료 기능이 CLI 작업 세션을 죽인다.

    판정 순서:
      1) CLI가 놓이는 위치면 무조건 아니다(가장 먼저 — 오탐의 대가가 가장 크다)
      2) 아는 Desktop 설치 위치 조각과 맞으면 맞다(버전·퍼블리셔·드라이브 무관)
      3) 둘 다 아니면(앞으로 설치 위치가 바뀌는 경우) 보이는 창을 가진 GUI인지로 판단
    """
    if not exe_path:
        return False
    p = _normalize_exe_path(exe_path)
    if any(marker in p for marker in _CLAUDE_CLI_PATH_MARKERS):
        return False
    if any(marker in p for marker in _CLAUDE_DESKTOP_PATH_MARKERS):
        return True
    return _process_has_visible_window(pid) if pid is not None else False


# 마지막으로 확인한 Claude Desktop 실행 파일 경로. 종료한 뒤에는 프로세스에서 경로를
# 읽을 수 없고, MSIX(스토어) 설치본은 꺼진 상태에서 설치 위치를 알아낼 방법이 마땅치
# 않다(WindowsApps 폴더는 권한이 막혀 목록 조회 자체가 불가 — 직접 확인했다).
# "닫고 → 작업하고 → 다시 켜기" 흐름에서 다시 못 켜는 문제가 실제로 났으므로,
# 켜져 있을 때 본 경로를 파일로도 남겨 앱을 껐다 켜도 이어지게 한다.
_last_known_claude_exe: str | None = None


def _claude_exe_memo_path() -> Path:
    d = Path.home() / ".leetkit-manager"
    d.mkdir(parents=True, exist_ok=True)
    return d / "claude_desktop_path"


def _remember_claude_exe(exe: str) -> None:
    global _last_known_claude_exe
    if exe == _last_known_claude_exe:
        return
    _last_known_claude_exe = exe
    try:
        _claude_exe_memo_path().write_text(exe, encoding="utf-8")
    except Exception:
        pass  # 기억 못 해도 켜져 있는 동안은 메모리 값으로 충분하다


def _recall_claude_exe() -> str | None:
    global _last_known_claude_exe
    if _last_known_claude_exe:
        return _last_known_claude_exe
    try:
        exe = _claude_exe_memo_path().read_text(encoding="utf-8").strip()
    except Exception:
        return None
    # 지워졌거나 버전이 올라 경로가 바뀌었을 수 있다 — 실제로 있는지 확인하고 쓴다.
    if exe and Path(exe).exists():
        _last_known_claude_exe = exe
        return exe
    return None


def claude_desktop_processes() -> list:
    """실행 중인 Claude Desktop 프로세스 목록(psutil.Process). 경로로 판별하므로
    Claude Code CLI는 절대 섞이지 않는다."""
    global _last_known_claude_exe
    try:
        import psutil
    except Exception:
        return []

    found = []
    try:
        # process_iter 자체도 환경·권한에 따라 던질 수 있다 — 여기서 예외가 새어나가면
        # 진단·마법사 흐름 전체가 죽는다. 프로세스 목록을 못 읽는 건 "판단 보류"이지
        # 치명적 실패가 아니다.
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                name = (proc.info.get("name") or "").lower()
                # macOS 실행 파일 이름은 확장자 없는 "Claude"다.
                if name not in ("claude.exe", "claude"):
                    continue
                if _is_claude_desktop_exe(proc.info.get("exe"), pid=proc.pid):
                    found.append(proc)
                    if proc.info.get("exe"):
                        _remember_claude_exe(proc.info["exe"])
            except Exception:
                continue
    except Exception:
        return []
    return found


def is_claude_desktop_running() -> bool:
    """Claude Desktop이 지금 떠 있는지.

    MCP 등록은 `claude_desktop_config.json`을 고치는 것뿐이고, Claude Desktop은 그 파일을
    "시작할 때" 읽는다 — 이미 실행 중이면 등록해도 도구가 안 보인다. 등록 직후 "이제 바로
    써보세요"라고만 안내하면 고객은 도구가 안 보이는 걸 보고 "설치가 안 됐다"고 판단한다
    (1인 운영에서 이건 그대로 환불·문의로 돌아온다). 실제로 떠 있을 때만 재시작을
    안내하려고 확인한다. 판단이 안 서면 False — 불필요한 안내로 겁주지 않는다."""
    return bool(claude_desktop_processes())


def _claude_desktop_aumid(exe_path: str) -> str | None:
    """MSIX(스토어) 설치본의 AppUserModelID를 실행 파일 경로에서 도출한다.
    `...\\WindowsApps\\Claude_1.25927.0.0_x64__pzs8sxrjxfjjc\\app\\claude.exe`
    → `Claude_pzs8sxrjxfjjc!Claude`. 실기기의 실제 AUMID와 일치함을 확인했다.
    MSIX 앱은 exe를 직접 실행하면 활성화 컨텍스트가 없어 실패할 수 있어서,
    `explorer shell:AppsFolder\\<AUMID>`로 띄우는 게 정석이다."""
    for parent in Path(exe_path).parents:
        folder = parent.name
        if "__" in folder and folder.lower().startswith("claude_"):
            head, _, publisher = folder.rpartition("__")
            name = head.split("_")[0]
            if name and publisher:
                return f"{name}_{publisher}!{name}"
    return None


def quit_claude_desktop(*, timeout: float = 10.0) -> bool:
    """실행 중인 Claude Desktop을 종료한다. 종료됐으면(또는 애초에 안 떠 있었으면) True.

    창 닫기로는 트레이에 남기 때문에 사용자가 "완전히 종료"를 어려워한다 — 그 단계를
    대신 해준다. 경로로 판별한 프로세스만 건드리므로 Claude Code CLI 세션은 절대
    함께 죽지 않는다(claude_desktop_processes 참고)."""
    procs = claude_desktop_processes()
    if not procs:
        return True

    import psutil

    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            pass
    _gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()  # 정상 종료를 안 받아들이면 강제 — 재시작이 목적이라 여기서 멈추면 안 된다
        except Exception:
            pass
    psutil.wait_procs(alive, timeout=3)
    return not claude_desktop_processes()


def launch_claude_desktop(exe_hint: str | None = None) -> bool:
    """Claude Desktop을 실행한다. macOS는 `open -a`, Windows MSIX는 AUMID, 그 외는 직접."""
    if sys.platform == "darwin":
        # .app 번들은 내부 실행 파일을 직접 띄우면 안 된다(런치 서비스를 거쳐야
        # Dock·활성화가 정상 동작한다). 설치 위치와 무관하게 앱 이름으로 띄운다.
        try:
            return subprocess.run(
                ["open", "-a", "Claude"], capture_output=True, timeout=15, env=child_env()
            ).returncode == 0
        except Exception:
            return False

    exe = exe_hint
    if not exe:
        for proc in claude_desktop_processes():
            exe = proc.info.get("exe")
            break
    # 종료한 직후엔 프로세스가 없으므로 켜져 있을 때 기억해둔 경로를 쓴다 —
    # 이게 없으면 MSIX 설치본은 "닫고 → 다시 켜기"에서 다시 못 켠다(직접 재현).
    if not exe:
        exe = _recall_claude_exe()
    if not exe:
        exe = _find_claude_desktop_exe()
    if not exe:
        return False

    aumid = _claude_desktop_aumid(exe)
    creationflags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
    if aumid:
        try:
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{aumid}"],
                creationflags=creationflags, close_fds=True, env=child_env(),
            )
            return True
        except Exception:
            pass
    try:
        subprocess.Popen([exe], creationflags=creationflags, close_fds=True, env=child_env())
        return True
    except Exception:
        return False


def _find_claude_desktop_exe() -> str | None:
    """실행 중이 아닐 때 설치 경로를 찾는다 — 고전 인스톨러 위치만 확실히 알 수 있다
    (MSIX는 WindowsApps 권한 때문에 목록 조회가 막힐 수 있어 실행 중일 때의 경로에 의존)."""
    if sys.platform == "darwin":
        app = Path("/Applications/Claude.app/Contents/MacOS/Claude")
        return str(app) if app.exists() else None
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "AnthropicClaude"
    if not base.exists():
        return None
    candidates = sorted(base.glob("app-*/claude.exe"), reverse=True) or sorted(base.glob("claude.exe"))
    return str(candidates[0]) if candidates else None


def restart_claude_desktop() -> dict:
    """MCP 등록을 반영하려면 Claude Desktop을 껐다 켜야 한다 — 그 두 단계를 한 번에.
    반환: {"ok": bool, "error": str|None}"""
    procs = claude_desktop_processes()
    exe_hint = None
    for proc in procs:
        exe_hint = proc.info.get("exe")
        break

    if procs and not quit_claude_desktop():
        return {"ok": False, "error": "Claude Desktop을 종료하지 못했습니다. 직접 종료한 뒤 다시 시도해주세요."}

    if not launch_claude_desktop(exe_hint):
        return {"ok": False, "error": "Claude Desktop을 다시 실행하지 못했습니다. 직접 실행해주세요."}
    return {"ok": True, "error": None}


def is_claude_desktop_installed() -> bool:
    """Claude Desktop이 이 컴퓨터에 설치돼 있는지(실행 중이 아니어도).

    MCP 등록은 설정 파일을 만드는 것뿐이라, 앱이 없어도 "등록 성공"이 뜬다 —
    사용자는 등록됐다고 믿는데 읽어갈 앱이 없다. 등록 대상으로 제안하기 전에 확인한다.
    판단 근거는 셋 중 하나면 충분하다: 지금 실행 중 / 실행 파일 발견 /
    설정 폴더 존재(한 번이라도 실행한 적 있음)."""
    if is_claude_desktop_running():
        return True
    if _find_claude_desktop_exe():
        return True
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata and (Path(appdata) / "Claude").exists():
            return True
        # MSIX 설치본은 실행 중이 아니면 WindowsApps를 못 읽는 경우가 있어, 시작 메뉴
        # 바로가기로도 확인한다.
        for root in filter(None, (os.environ.get("APPDATA"), os.environ.get("ProgramData"))):
            if list(Path(root, "Microsoft", "Windows", "Start Menu", "Programs").glob("Claude*")):
                return True
    else:
        if (Path.home() / "Library" / "Application Support" / "Claude").exists():
            return True
        if Path("/Applications/Claude.app").exists():
            return True
    return False


def is_claude_code_installed() -> bool:
    """Claude Code CLI가 설치돼 있는지 — `claude` 실행 파일 또는 `~/.claude.json`."""
    found = shutil.which("claude")
    if found and Path(found).is_absolute():
        return True
    return (Path.home() / ".claude.json").exists() or (Path.home() / ".claude").exists()


# ── ChatGPT 앱 ──────────────────────────────────────────────────────
# 2026-07-09 통합 이후 ChatGPT 앱은 Codex CLI와 같은 `~/.codex/config.toml`을
# 읽는다 — codex 타겟에 등록하면 ChatGPT 앱에서도 Lens 도구가 그대로 뜬다. 그리고 앱은
# 그 파일을 "켤 때" 읽고, 켜져 있는 동안 Lens 프로세스를 자기가 쥔다. 즉 Claude Desktop과
# 똑같은 두 문제가 생긴다:
#   1) 등록·업데이트해도 이미 떠 있는 쪽은 예전 상태로 계속 돈다 → 껐다 켜야 반영된다
#   2) Lens 파일을 쥐고 있어서 uv 설치·삭제가 "파일 사용 중"으로 막힌다
# 그래서 Claude에 있는 (종료 · 실행 · 재시작) 세트를 같은 모양으로 갖춘다.
#
# 실기기(Windows 11)에서 확인한 사실 — 헷갈리는 지점이 있다:
#   실행 파일  C:\\Program Files\\WindowsApps\\OpenAI.Codex_<버전>_x64__<퍼블리셔>\\app\\ChatGPT.exe
#   프로세스   ChatGPT.exe 가 여러 개(창·렌더러) — 전부 같은 exe 경로다
#   AUMID     OpenAI.Codex_<퍼블리셔>!App                 (통합 앱)
#             OpenAI.ChatGPT-Desktop_<퍼블리셔>!ChatGPT   (통합 전 "ChatGPT Classic")
# 패키지 이름은 OpenAI.Codex 인데 실행 파일은 ChatGPT.exe 다. 통합으로 Codex 패키지가
# ChatGPT 앱을 품은 것이라 둘 다 같은 앱을 가리킨다.
#
# Codex **CLI**는 이 앱이 아니다. 이름이 달라서(codex.exe vs ChatGPT.exe) 섞일 일은
# 없지만, Claude 쪽에서 이름만 보고 판별했다가 CLI 세션을 죽인 전례가 있으므로
# 여기서도 경로로 판별한다.
_CHATGPT_DESKTOP_PATH_MARKERS = (
    "/windowsapps/openai.codex",            # Windows MSIX — 통합 앱(ChatGPT + Codex)
    "/windowsapps/openai.chatgpt-desktop",  # Windows MSIX — 통합 전 구 앱
    "/chatgpt.app/",                        # macOS — /Applications/ChatGPT.app/...
)


def _msix_app_id(pkg_dir: Path) -> str | None:
    """MSIX 패키지 폴더의 AppxManifest.xml에서 `<Application Id="...">`를 읽는다.
    실기기에서 WindowsApps 안의 이 파일은 읽을 수 있음을 확인했다(폴더 목록 조회가
    막혀 있어도 파일 경로를 알면 열린다). 못 읽으면 None."""
    try:
        import xml.etree.ElementTree as ET

        root = ET.parse(str(pkg_dir / "AppxManifest.xml")).getroot()
        for el in root.iter():
            if el.tag.rpartition("}")[2] == "Application" and el.get("Id"):
                return el.get("Id")
    except Exception:
        return None
    return None


# AppxManifest를 못 읽을 때의 대비책 — 실기기에서 확인한 값.
_MSIX_APP_ID_FALLBACK = {"openai.codex": "App", "openai.chatgpt-desktop": "ChatGPT"}


def _msix_aumid(exe_path: str) -> str | None:
    """MSIX 실행 파일 경로 → AppUserModelID.
    `...\\WindowsApps\\OpenAI.Codex_26.810.7004.0_x64__2p2nqsd0c76g0\\app\\ChatGPT.exe`
    → `OpenAI.Codex_2p2nqsd0c76g0!App`. app id는 패키지마다 다르므로(App / ChatGPT)
    Claude처럼 패키지 이름에서 유추하면 안 된다 — 매니페스트를 읽고, 안 되면 표를 쓴다."""
    for parent in Path(exe_path).parents:
        folder = parent.name
        if "__" not in folder:
            continue
        head, _, publisher = folder.rpartition("__")
        name = head.split("_")[0]
        if not (name and publisher):
            continue
        app_id = _msix_app_id(parent) or _MSIX_APP_ID_FALLBACK.get(name.lower())
        if not app_id:
            return None
        return f"{name}_{publisher}!{app_id}"
    return None


def _is_chatgpt_desktop_exe(exe_path: str | None, *, pid: int | None = None) -> bool:
    """실행 파일 경로가 ChatGPT 앱인지. 아는 설치 위치 조각으로 판별하고,
    앞으로 위치가 바뀌면 보이는 창을 가진 GUI인지로 판단한다(Claude와 같은 순서)."""
    if not exe_path:
        return False
    p = _normalize_exe_path(exe_path)
    if any(marker in p for marker in _CHATGPT_DESKTOP_PATH_MARKERS):
        return True
    return _process_has_visible_window(pid) if pid is not None else False


# 종료한 뒤에는 프로세스에서 경로를 읽을 수 없다 — Claude와 같은 이유로 켜져 있을 때
# 본 경로를 파일에도 남긴다(MSIX는 꺼진 상태에서 설치 위치를 찾기 어렵다).
_last_known_chatgpt_exe: str | None = None


def _chatgpt_exe_memo_path() -> Path:
    d = Path.home() / ".leetkit-manager"
    d.mkdir(parents=True, exist_ok=True)
    return d / "chatgpt_desktop_path"


def _remember_chatgpt_exe(exe: str) -> None:
    global _last_known_chatgpt_exe
    if exe == _last_known_chatgpt_exe:
        return
    _last_known_chatgpt_exe = exe
    try:
        _chatgpt_exe_memo_path().write_text(exe, encoding="utf-8")
    except Exception:
        pass  # 기억 못 해도 켜져 있는 동안은 메모리 값으로 충분하다


def _recall_chatgpt_exe() -> str | None:
    global _last_known_chatgpt_exe
    if _last_known_chatgpt_exe:
        return _last_known_chatgpt_exe
    try:
        exe = _chatgpt_exe_memo_path().read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if exe and Path(exe).exists():
        _last_known_chatgpt_exe = exe
        return exe
    return None


def chatgpt_desktop_processes() -> list:
    """실행 중인 ChatGPT 앱 프로세스 목록(psutil.Process).
    창·렌더러가 여러 개라 결과도 보통 여러 개다 — 전부 같은 앱이다."""
    try:
        import psutil
    except Exception:
        return []

    found = []
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                name = (proc.info.get("name") or "").lower()
                # macOS 실행 파일 이름은 확장자 없는 "ChatGPT"다.
                if name not in ("chatgpt.exe", "chatgpt"):
                    continue
                if _is_chatgpt_desktop_exe(proc.info.get("exe"), pid=proc.pid):
                    found.append(proc)
                    if proc.info.get("exe"):
                        _remember_chatgpt_exe(proc.info["exe"])
            except Exception:
                continue
    except Exception:
        return []
    return found


def is_chatgpt_desktop_running() -> bool:
    """ChatGPT 앱이 지금 떠 있는지. 판단이 안 서면 False —
    불필요한 재시작 안내로 겁주지 않는다(is_claude_desktop_running과 같은 원칙)."""
    return bool(chatgpt_desktop_processes())


def _is_helper_process(proc) -> bool:
    """Electron 계열 앱의 보조 프로세스(렌더러·GPU·네트워크·crashpad)인지 —
    이들 명령줄에는 `--type=...`이 붙는다.

    실기기에서 확인한 사고: ChatGPT 앱을 껐더니 창은 사라졌는데 보조 프로세스 몇 개가
    잠깐 남았다(10개 중 4개). 그걸 "아직 안 껐다"로 세는 바람에 종료가 실패한 것으로
    보고 재실행을 건너뛰어, 사용자 눈에는 **아무 일도 일어나지 않았다.** 종료 판정은
    메인 프로세스만 본다(보조는 곧 알아서 사라지고 설정 파일을 쥐지도 않는다)."""
    try:
        return any(str(arg).startswith("--type=") for arg in (proc.cmdline() or [])[1:])
    except Exception:
        # 명령줄을 못 읽으면 메인으로 보수적으로 본다 — 살아 있으면 종료 실패로 잡힌다.
        return False


def _chatgpt_main_processes() -> list:
    return [p for p in chatgpt_desktop_processes() if not _is_helper_process(p)]


def quit_chatgpt_desktop(*, timeout: float = 10.0) -> bool:
    """실행 중인 ChatGPT 앱을 종료한다. 종료됐으면(또는 애초에 안 떠 있었으면) True.

    ChatGPT 앱 안에서 Codex 작업이 돌고 있으면 그것도 함께 끊긴다 — Claude Desktop과
    같은 성질이라, 호출하는 쪽에서 반드시 사용자 확인을 먼저 받는다."""
    procs = chatgpt_desktop_processes()
    if not procs:
        return True

    import psutil

    # 메인 → 보조 순서로 끈다. 메인이 먼저 죽으면 보조는 대개 알아서 따라 죽는다.
    ordered = [p for p in procs if not _is_helper_process(p)]
    ordered += [p for p in procs if _is_helper_process(p)]
    for proc in ordered:
        try:
            proc.terminate()
        except Exception:
            pass
    _gone, alive = psutil.wait_procs(ordered, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()  # 재시작이 목적이라 여기서 멈추면 안 된다
        except Exception:
            pass
    psutil.wait_procs(alive, timeout=3)

    # 남은 보조 프로세스가 사라질 시간을 조금 준다(최대 3초). 메인이 없으면 껐다고 본다.
    for _ in range(6):
        if not _chatgpt_main_processes():
            return True
        time.sleep(0.5)
    return False


def _find_chatgpt_desktop_exe() -> str | None:
    """실행 중이 아닐 때 설치 경로를 찾는다. Windows는 MSIX라 WindowsApps 목록 조회가
    권한으로 막힐 수 있어(그래서 켜져 있을 때 기억해둔 경로에 의존한다) 여기서는
    접근 가능한 경우만 훑는다."""
    if sys.platform == "darwin":
        # 계정에만 설치한 경우(~/Applications)도 본다 — /Applications 만 보면 놓친다.
        for base in (Path("/Applications"), Path.home() / "Applications"):
            app = base / "ChatGPT.app" / "Contents" / "MacOS" / "ChatGPT"
            if app.exists():
                return str(app)
        return None
    if sys.platform != "win32":
        return None
    base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WindowsApps"
    try:
        for pattern in ("OpenAI.Codex_*", "OpenAI.ChatGPT-Desktop_*"):
            for pkg in sorted(base.glob(pattern), reverse=True):
                exe = pkg / "app" / "ChatGPT.exe"
                if exe.exists():
                    return str(exe)
    except Exception:
        return None
    return None


def launch_chatgpt_desktop(exe_hint: str | None = None) -> bool:
    """ChatGPT 앱을 실행한다. macOS는 `open -a`, Windows MSIX는 AUMID."""
    if sys.platform == "darwin":
        # .app 번들은 내부 실행 파일을 직접 띄우면 안 된다(런치 서비스를 거쳐야
        # Dock·활성화가 정상 동작한다).
        try:
            return subprocess.run(
                ["open", "-a", "ChatGPT"], capture_output=True, timeout=15, env=child_env()
            ).returncode == 0
        except Exception:
            return False

    exe = exe_hint
    if not exe:
        for proc in chatgpt_desktop_processes():
            exe = proc.info.get("exe")
            break
    # 종료한 직후엔 프로세스가 없으므로 켜져 있을 때 기억해둔 경로를 쓴다.
    if not exe:
        exe = _recall_chatgpt_exe()
    if not exe:
        exe = _find_chatgpt_desktop_exe()
    if not exe:
        return False

    # MSIX 앱은 exe를 직접 실행하면 활성화 컨텍스트가 없어 실패할 수 있다 —
    # `explorer shell:AppsFolder\<AUMID>`가 정석이다.
    aumid = _msix_aumid(exe)
    creationflags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
    if aumid:
        try:
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{aumid}"],
                creationflags=creationflags, close_fds=True, env=child_env(),
            )
            return True
        except Exception:
            pass
    try:
        subprocess.Popen([exe], creationflags=creationflags, close_fds=True, env=child_env())
        return True
    except Exception:
        return False


def restart_chatgpt_desktop() -> dict:
    """codex 타겟 등록·업데이트를 반영하려면 ChatGPT 앱을 껐다 켜야 한다 — 두 단계를 한 번에.
    반환: {"ok": bool, "error": str|None}"""
    procs = chatgpt_desktop_processes()
    exe_hint = None
    for proc in procs:
        exe_hint = proc.info.get("exe")
        break

    if procs and not quit_chatgpt_desktop():
        return {"ok": False, "error": "ChatGPT를 종료하지 못했습니다. 직접 종료한 뒤 다시 시도해주세요."}

    if not launch_chatgpt_desktop(exe_hint):
        return {"ok": False, "error": "ChatGPT를 다시 실행하지 못했습니다. 직접 실행해주세요."}
    return {"ok": True, "error": None}


def is_chatgpt_desktop_installed() -> bool:
    """ChatGPT 앱이 이 컴퓨터에 있는지(실행 중이 아니어도).
    실행 중 / 실행 파일 발견 / 기억해둔 경로 / 시작 메뉴·패키지 폴더 중 하나면 충분하다."""
    if is_chatgpt_desktop_running():
        return True
    if _find_chatgpt_desktop_exe():
        return True
    if _recall_chatgpt_exe():
        return True
    if sys.platform == "win32":
        # MSIX는 꺼져 있으면 WindowsApps를 못 읽는 경우가 있어 시작 메뉴로도 확인한다.
        for root in filter(None, (os.environ.get("APPDATA"), os.environ.get("ProgramData"))):
            if list(Path(root, "Microsoft", "Windows", "Start Menu", "Programs").glob("ChatGPT*")):
                return True
        local = os.environ.get("LOCALAPPDATA")
        if local:
            try:
                packages = Path(local) / "Packages"
                for pattern in ("OpenAI.Codex_*", "OpenAI.ChatGPT-Desktop_*"):
                    if list(packages.glob(pattern)):
                        return True
            except Exception:
                pass
    else:
        for base in (Path("/Applications"), Path.home() / "Applications"):
            if (base / "ChatGPT.app").exists():
                return True
    return False


# ── MCP 호스트 앱(껐다 켜야 반영되는 GUI 앱) 묶음 ─────────────────────────────
# Claude Desktop과 ChatGPT는 성질이 같다 — 켤 때 설정을 읽고, 켜져 있는 동안 Lens
# 프로세스를 쥔다. UI가 "어느 앱이냐"를 자리마다 갈라 쓰지 않도록 여기서 묶어 다룬다.
# Claude Code CLI·Codex CLI는 여기 없다(새 대화를 열면 알아서 새로 뜬다).
# 함수를 객체로 담지 않고 **이름으로** 담는다 — 객체를 담아두면 나중에 함수를 갈아끼운
# 쪽(테스트·진단)에서 이 표만 옛 함수를 계속 부른다. 조용히 어긋나는 종류의 버그다.
_HOST_APPS: dict[str, tuple] = {
    # id: (표시 이름, 실행 중 판별, 종료, 실행, 설치 여부)
    "claude-desktop": (
        "Claude Desktop",
        "is_claude_desktop_running",
        "quit_claude_desktop",
        "launch_claude_desktop",
        "is_claude_desktop_installed",
    ),
    "chatgpt": (
        "ChatGPT",
        "is_chatgpt_desktop_running",
        "quit_chatgpt_desktop",
        "launch_chatgpt_desktop",
        "is_chatgpt_desktop_installed",
    ),
}

_HOST_RUNNING, _HOST_QUIT, _HOST_LAUNCH, _HOST_INSTALLED = 1, 2, 3, 4


def _host_fn(host_id: str, slot: int):
    """호스트 앱의 동작 함수를 부를 때 찾아온다(위 표는 이름만 들고 있다)."""
    return globals()[_HOST_APPS[host_id][slot]]


def host_app_label(host_id: str) -> str:
    spec = _HOST_APPS.get(host_id)
    return spec[0] if spec else host_id


def running_host_apps() -> list[dict]:
    """지금 떠 있는 호스트 앱 목록 — [{"id", "label"}]. UI가 이걸로 안내 문구를 만든다."""
    out = []
    for host_id, spec in _HOST_APPS.items():
        try:
            if _host_fn(host_id, _HOST_RUNNING)():
                out.append({"id": host_id, "label": spec[0]})
        except Exception:
            continue  # 판단 못 하는 앱은 건너뛴다(잘못된 안내보다 없는 게 낫다)
    return out


def installed_host_apps() -> list[dict]:
    """이 컴퓨터에 있는 호스트 앱 목록 — [{"id", "label", "running"}]."""
    out = []
    for host_id, spec in _HOST_APPS.items():
        try:
            if _host_fn(host_id, _HOST_INSTALLED)():
                out.append(
                    {
                        "id": host_id,
                        "label": spec[0],
                        "running": _host_fn(host_id, _HOST_RUNNING)(),
                    }
                )
        except Exception:
            continue
    return out


def _host_ids(ids: "list[str] | None") -> list[str]:
    if ids is None:
        return [h["id"] for h in running_host_apps()]
    return [i for i in ids if i in _HOST_APPS]


def quit_host_apps(ids: "list[str] | None" = None) -> dict:
    """호스트 앱을 종료한다(기본값: 지금 켜져 있는 것 전부).
    반환: {"ok", "quit": [label], "failed": [label], "error": str|None}"""
    done, failed = [], []
    for host_id in _host_ids(ids):
        label = _HOST_APPS[host_id][0]
        try:
            (done if _host_fn(host_id, _HOST_QUIT)() else failed).append(label)
        except Exception:
            failed.append(label)
    error = None
    if failed:
        error = f"{', '.join(failed)}을(를) 종료하지 못했습니다. 직접 종료한 뒤 다시 시도해주세요."
    return {"ok": not failed, "quit": done, "failed": failed, "error": error}


def launch_host_apps(ids: "list[str] | None" = None) -> dict:
    """호스트 앱을 실행한다. ids를 안 주면 설치된 것 전부(보통은 방금 종료한 목록을 준다).
    반환: {"ok", "launched": [label], "failed": [label], "error": str|None}"""
    if ids is None:
        ids = [h["id"] for h in installed_host_apps()]
    done, failed = [], []
    for host_id in _host_ids(ids):
        label = _HOST_APPS[host_id][0]
        try:
            (done if _host_fn(host_id, _HOST_LAUNCH)() else failed).append(label)
        except Exception:
            failed.append(label)
    error = None
    if failed:
        error = f"{', '.join(failed)}을(를) 다시 실행하지 못했습니다. 직접 실행해주세요."
    return {"ok": not failed, "launched": done, "failed": failed, "error": error}


def restart_host_apps(ids: "list[str] | None" = None) -> dict:
    """호스트 앱을 껐다 켠다 — 등록·업데이트를 반영하는 마지막 단계.
    반환: {"ok", "restarted": [label], "error": str|None}

    **켜져 있는 것만** 대상으로 한다. 안 켜져 있는 앱까지 우리가 띄우면, 사용자가 열지도
    않은 앱이 갑자기 뜬다(ids를 명시해도 이 규칙은 같다)."""
    targets = [i for i in _host_ids(ids) if _host_fn(i, _HOST_RUNNING)()]
    if not targets:
        return {"ok": True, "restarted": [], "error": None}

    restarted, failed = [], []
    for host_id in targets:
        label = _HOST_APPS[host_id][0]
        try:
            if not _host_fn(host_id, _HOST_QUIT)():
                failed.append(label)
                continue
            (restarted if _host_fn(host_id, _HOST_LAUNCH)() else failed).append(label)
        except Exception:
            failed.append(label)
    error = None
    if failed:
        error = f"{', '.join(failed)}을(를) 다시 시작하지 못했습니다. 직접 껐다 켜주세요."
    return {"ok": not failed, "restarted": restarted, "error": error}


def is_codex_installed() -> bool:
    """codex 타겟(= Codex CLI **와** ChatGPT 앱)을 쓸 수 있는 컴퓨터인지.

    설치도 안 된 걸 MCP 등록 대상 체크박스로 보여주면 혼란만 주므로 UI가 이걸로
    걸러낸다. 그런데 이 타겟은 CLI 전용이 아니다 — ChatGPT 앱이 같은
    `~/.codex/config.toml`을 읽으므로, **CLI를 한 번도 깐 적 없고 ChatGPT 앱만 쓰는
    사람**도 대상이다. `~/.codex` 유무만 보던 동안은 그 사람에게 체크박스가 "미설치"로
    떠서, 정작 쓸 수 있는 경로를 고를 수 없었다."""
    if shutil.which("codex"):
        return True
    if (Path.home() / ".codex").exists():
        return True
    return is_chatgpt_desktop_installed()


def list_installed_tools() -> dict[str, str]:
    """`uv tool list` 결과를 {package_name: version}으로. 실패하면 빈 딕셔너리."""
    result = run_cli([resolve_uv_command(), "tool", "list"], timeout=15.0)
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


# ── 독립 exe 자체 업데이트 ──────────────────────────────────────────
#
# `uv tool install`로 깔린 버전은 self_update()가 PyPI+uv로 그대로 처리하면 되지만,
# PyInstaller 단일 exe로 받은 사용자는 uv가 관리하는 실행 파일이 아니라 exe 그
# 자체가 곧 "설치본"이다 — 그래서 이쪽은 GitHub Release에서 새 exe를 받아 지금
# 실행 중인 파일 자체를 바꿔치기해야 한다(uv tool install이 아예 무관함).


def is_frozen_exe() -> bool:
    """PyInstaller 단일 exe로 실행 중인지 — 자기 업데이트 방식이 이 경우와
    `uv tool install`로 깔린 일반 스크립트 실행 사이에서 완전히 다르다."""
    return bool(getattr(sys, "frozen", False))


def latest_github_release(*, timeout: float = _GITHUB_RELEASE_TIMEOUT) -> dict | None:
    """GitHub Releases API에서 최신 릴리스의 버전과 exe 자산 다운로드 URL을 가져온다.
    반환: {"version": "0.1.1", "exe_url": "https://.../LeetKitManager.exe"} 또는 실패 시 None."""
    try:
        resp = httpx.get(
            _GITHUB_RELEASES_LATEST_URL,
            timeout=timeout,
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        data = resp.json()
        tag = data.get("tag_name") or ""
        version = tag[1:] if tag.startswith("v") else tag
        assets = data.get("assets", [])
        exe_asset = next((a for a in assets if a.get("name") == "LeetKitManager.exe"), None)
        if not version or not exe_asset:
            return None
        sha_asset = next((a for a in assets if a.get("name") == _EXE_SHA256_ASSET), None)
        return {
            "version": version,
            "exe_url": exe_asset.get("browser_download_url"),
            "sha256_url": sha_asset.get("browser_download_url") if sha_asset else None,
        }
    except Exception:
        return None


def fetch_expected_sha256(url: str, *, timeout: float = _GITHUB_RELEASE_TIMEOUT) -> str | None:
    """릴리스에 첨부된 체크섬 파일에서 기대 해시를 읽는다.
    `sha256sum` 형식(`<hash>  <filename>`)과 해시만 있는 형식 둘 다 받는다."""
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        first = resp.text.strip().split("\n")[0].strip()
        candidate = first.split()[0] if first else ""
        candidate = candidate.lower()
        if len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate):
            return candidate
        return None
    except Exception:
        return None


def sha256_of_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


def download_file(url: str, dest: Path, *, timeout: float = _EXE_DOWNLOAD_TIMEOUT) -> bool:
    """스트리밍 다운로드 — exe가 수십MB라 한 번에 메모리에 올리지 않는다."""
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        return True
    except Exception:
        return False


def _hide_file(path: Path) -> None:
    """Windows 탐색기에서 안 보이게 숨김 속성을 준다.

    자체 업데이트가 남기는 `<exe>.exe.old` 때문이다. 지우는 건 새 프로세스가 뜰 때
    시도하는데, 그 순간 백신이 방금 이름이 바뀐 파일을 검사하고 있으면 삭제가 막힌다
    — 그러면 다음 실행 때까지 사용자 폴더에 남는다. 실제로 "이 파일 뭔가요?"라는
    문의가 반복됐다.

    숨김은 지우기의 대체재가 아니라 보험이다. 삭제는 그대로 시도하고, 실패해 남더라도
    눈에는 안 띄게 한다.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


def replace_running_exe(new_exe_path: Path) -> ProcessResult:
    """지금 실행 중인 exe 자신을 new_exe_path의 내용으로 바꿔치고 재실행한다.

    Windows는 실행 중인 파일도 '이름 변경'은 허용한다(삭제·덮어쓰기만 막는다) — 그래서
    (1) 현재 exe를 .old로 rename, (2) 새 exe를 원래 이름으로 복사, (3) 새 exe 실행,
    순서면 지금 프로세스가 아직 살아있는 동안에도 파일 교체가 끝난다. 남은 .old는
    지금은 지워지지 않고(이 프로세스가 아직 그 이름의 파일 핸들을 쥐고 있음) 다음
    실행 때 cleanup_old_exe_backup()이 정리한다.
    """
    current_exe = Path(sys.executable)
    backup = current_exe.with_name(current_exe.stem + ".exe.old")
    try:
        if backup.exists():
            backup.unlink()
        current_exe.rename(backup)
        # 이 프로세스가 살아있는 한 저 파일은 못 지운다 — 그동안만이라도 안 보이게.
        _hide_file(backup)
        shutil.copy2(new_exe_path, current_exe)
        creationflags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
        # --wait-for-exit: 새 프로세스가 지금 이 프로세스의 종료를 기다렸다가 뜬다.
        # 이게 없으면 새 프로세스가 곧바로 중복 실행 방지에 걸린다 — 이 프로세스가
        # 아직 몇 초 더 살아 뮤텍스와 창을 쥐고 있어서, 새 쪽이 "이미 실행 중"으로
        # 판단하고 스스로 종료한 뒤 이 프로세스마저 닫히면 아무것도 안 남는다
        # (화면엔 "다시 시작합니다"만 뜨고 실제로는 아무 일도 안 일어났다).
        # env: PyInstaller 부트로더 변수를 떼고 넘긴다. 안 떼면 새 exe가 압축을 새로
        # 풀지 않고 **지금 이 프로세스의 임시 폴더를 그대로 쓴다** — 그러면 (1) 새
        # 버전이 아니라 옛 코드가 돌고, (2) 이 프로세스가 끝나면서 그 폴더를 지워
        # 새 쪽이 곧바로 죽는다. 실제로 이렇게 죽었다:
        #   FileNotFoundError: Cannot find Microsoft.Web.WebView2.Core.dll
        # --wait-for-exit이 "부모가 죽은 뒤에" 창을 열게 만들어서 100% 재현됐다.
        # (process_runner.child_env의 설명·재현 결과 참고)
        subprocess.Popen(
            [str(current_exe), "--wait-for-exit", str(os.getpid())],
            creationflags=creationflags,
            close_fds=True,
            env=child_env(),
        )
        return ProcessResult(
            cmd=[str(current_exe)], exit_code=0, stdout="", stderr="", timed_out=False, duration_s=0.0
        )
    except Exception as e:
        return ProcessResult(
            cmd=[str(current_exe)], exit_code=1, stdout="", stderr=str(e), timed_out=False, duration_s=0.0, error=str(e)
        )


def relaunch_after_exit() -> bool:
    """지금 프로세스가 끝난 뒤 새 버전을 띄우도록 예약한다. 성공하면 True.

    `uv tool install`로 깐 버전용 — 단일 exe는 replace_running_exe가 파일을 바꿔치는
    김에 같이 띄운다. 이쪽은 예전엔 아무것도 안 해서, 업데이트를 누르면 "앱을 다시
    시작합니다"라고 안내하고는 그냥 닫히기만 했다(윈도우·맥 공통).

    새 프로세스는 `--wait-for-exit <pid>`로 이 프로세스의 종료를 기다린다 — 안 그러면
    중복 실행 방지에 걸려 스스로 종료하고, 곧이어 이 프로세스도 닫혀 아무것도 안 남는다.
    """
    command = resolve_lens_command("leetkit-manager")
    creationflags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
    try:
        subprocess.Popen(
            [command, "gui", "--wait-for-exit", str(os.getpid())],
            creationflags=creationflags,
            close_fds=True,
            start_new_session=(sys.platform != "win32"),
            env=child_env(),
        )
        return True
    except Exception:
        return False


def cleanup_old_exe_backup() -> None:
    """이전 자체 업데이트가 남긴 `<exe>.exe.old` 정리. 새로(교체된 뒤) 뜬 exe가 시작할
    때 시도한다.

    한 번만 시도하면 안 된다. 옛 프로세스가 막 끝난 직후라 파일이 잠깐 더 잠겨 있을 수
    있고(특히 백신이 방금 이름이 바뀐 파일을 검사 중일 때), 그 한 번이 실패하면 다음
    실행 때까지 사용자 폴더에 정체불명의 파일이 남는다 — "이거 뭔가요?" 문의의 정체가
    이것이었다. 잠깐씩 쉬며 몇 번 더 두드린다.

    그래도 안 지워지면 숨김 속성이라도 걸어 눈에서 치운다. 못 지우는 것보다 나쁜 건
    못 지운 걸 사용자가 보는 것이다.
    """
    if not is_frozen_exe():
        return
    import time

    current_exe = Path(sys.executable)
    backup = current_exe.with_name(current_exe.stem + ".exe.old")
    if not backup.exists():
        return

    for attempt in range(5):
        try:
            backup.unlink(missing_ok=True)
            return
        except Exception:
            # 0.2초씩 늘려가며 총 3초쯤 기다린다. 앱 시작을 눈에 띄게 늦추지 않으면서
            # 백신 검사 한 번이 끝나기엔 충분한 시간이다.
            time.sleep(0.2 * (attempt + 1))
    _hide_file(backup)

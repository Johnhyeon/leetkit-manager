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
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from leetkit_manager.process_runner import ProcessResult, run_cli, run_cli_streaming

_PYPI_TIMEOUT = 10.0
_INSTALL_TIMEOUT = 120.0  # PyPI 다운로드가 걸리므로 doctor(30초)보다 넉넉하게.
_UV_INSTALL_TIMEOUT = 120.0
_GITHUB_RELEASE_TIMEOUT = 10.0
_EXE_DOWNLOAD_TIMEOUT = 120.0
_EXE_SHA256_ASSET = "LeetKitManager.exe.sha256"
_GITHUB_RELEASES_LATEST_URL = "https://api.github.com/repos/Johnhyeon/leetkit-manager/releases/latest"


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


def latest_pypi_version(package_name: str, *, timeout: float = _PYPI_TIMEOUT) -> str | None:
    """PyPI JSON API에서 최신 버전 조회. 실패해도 예외를 던지지 않고 None."""
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


def _infer_python_for_script(script_path: Path) -> Path | None:
    """pip entry-point 스크립트(.exe) 경로에서 같이 설치된 python.exe를 추정한다.
    표준 Windows 설치는 Scripts/의 부모 폴더에 python.exe가 있고, venv는 Scripts/
    안에 같이 있다 — 두 레이아웃 다 지원."""
    for candidate in (script_path.parent / "python.exe", script_path.parent.parent / "python.exe"):
        if candidate.exists():
            return candidate
    return None


def uninstall_legacy_pip_shadow(package_name: str, shadow_path: Path, *, timeout: float = _INSTALL_TIMEOUT) -> ProcessResult:
    """옛 pip 설치 잔재를 그 실행 파일과 같이 있는 python.exe로 pip uninstall한다.
    같이 있는 python.exe를 못 찾으면(레이아웃을 못 알아본 경우) 엉뚱한 Python
    환경에서 잘못 실행하는 대신 안전하게 실패로 반환한다."""
    python_exe = _infer_python_for_script(shadow_path)
    if python_exe is None:
        return ProcessResult(
            cmd=["pip", "uninstall", package_name],
            exit_code=1, timed_out=False, duration_s=0.0, stdout="",
            stderr=f"'{shadow_path}' 옆에서 python.exe를 찾을 수 없어 자동으로 지울 수 없습니다. 수동으로 삭제해주세요.",
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


def version_gt(latest: str, current: str) -> bool:
    """semver 비교. 실패 시 단순 문자열 비교 fallback(각 Lens의 update-check 로직과 동일)."""
    try:
        from packaging.version import Version

        return Version(latest) > Version(current)
    except Exception:
        return bool(latest) and latest != current


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
            return subprocess.run(["open", "-a", "Claude"], capture_output=True, timeout=15).returncode == 0
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
                creationflags=creationflags, close_fds=True,
            )
            return True
        except Exception:
            pass
    try:
        subprocess.Popen([exe], creationflags=creationflags, close_fds=True)
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


def is_codex_installed() -> bool:
    """Codex CLI가 이 컴퓨터에 있는지 — 설치도 안 된 걸 MCP 등록 대상 체크박스로 보여주면
    혼란만 주므로, UI가 이걸로 걸러서 보여줄지 말지 정한다. PATH 탐색과
    `~/.codex` 존재 여부(설정 폴더는 있는데 PATH에 없는 경우도 커버) 둘 다 확인."""
    if shutil.which("codex"):
        return True
    return (Path.home() / ".codex").exists()


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
        shutil.copy2(new_exe_path, current_exe)
        creationflags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
        subprocess.Popen([str(current_exe)], creationflags=creationflags, close_fds=True)
        return ProcessResult(
            cmd=[str(current_exe)], exit_code=0, stdout="", stderr="", timed_out=False, duration_s=0.0
        )
    except Exception as e:
        return ProcessResult(
            cmd=[str(current_exe)], exit_code=1, stdout="", stderr=str(e), timed_out=False, duration_s=0.0, error=str(e)
        )


def cleanup_old_exe_backup() -> None:
    """이전 자체 업데이트가 남긴 `<exe>.exe.old` 정리. 새로(교체된 뒤) 뜬 exe가 시작할
    때 한 번 시도한다 — 그 시점엔 더 이상 아무 프로세스도 그 파일을 쥐고 있지 않아
    삭제가 된다(교체 직후, 옛 프로세스가 아직 살아있는 동안은 실패해도 무시)."""
    if not is_frozen_exe():
        return
    current_exe = Path(sys.executable)
    backup = current_exe.with_name(current_exe.stem + ".exe.old")
    try:
        backup.unlink(missing_ok=True)
    except Exception:
        pass

"""고객 지원 문의용 번들 — 로그·상태 파일만 골라 압축한다.

전체 홈 디렉터리를 통째로 압축하지 않는다 — StockLens/DartLens의 `license.key`,
TelegramLens의 `session.session`/`credentials.json`처럼 절대 밖으로 나가면 안 되는
파일이 로그와 같은 폴더에 섞여 있기 때문이다. 여기서는 안전 목록(로그·상태 JSON만)에
있는 파일만 골라 담고, summary.txt(세 Lens 진단 요약, redaction 적용)를 함께 넣는다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from leetkit_manager import orchestrator, redaction
from leetkit_manager.lens_contract import LENSES

SUPPORT_EMAIL = "osy980315@gmail.com"

# zip 안 폴더 이름(_safe_files가 정하는 접두어) → 화면에 쓰는 Lens 이름.
_LENS_DISPLAY_BY_DIR = {
    "stocklens": "StockLens",
    "dartlens": "DartLens",
    "telegramlens": "TelegramLens",
}

# Lens 3개 순차 × 실제 데이터소스 연결 확인.
#
# 처음엔 12초로 잡았다가 실측에서 되돌렸다 — 이 PC에서 telegramlens-doctor 한 번이
# 6.2초였고(텔레그램 DB를 훑는다), --online 을 모르는 Lens는 옵션을 빼고 한 번 더
# 물어보므로 두 번 돈다. 사양이 낮거나 수집한 메시지가 많은 PC에서는 12초를 넘긴다.
# 실제로 이 PC의 번들 생성에서도 한 번 넘겨서 멀쩡한 TelegramLens가 실패로 찍혔다.
# 내 PC에서 아슬아슬한 값은 남의 PC에서 틀린 값이다.
#
# 그래도 상한은 둔다 — 네트워크가 죽은 PC에서 기본 30초를 셋이 다 쓰면 90초 넘게
# 멈춘 것처럼 보이고, 사용자는 그쯤이면 창을 닫는다.
_DIAGNOSIS_TIMEOUT = 25.0

_RECENT_LOG_FILES = 7  # 로그 파일이 날짜별로 쌓이는 Lens는 최근 N개만 담는다.
_MAX_CLAUDE_LOG_BYTES = 20 * 1024 * 1024  # Claude 쪽 로그는 회전 없이 계속 자라므로 안전 상한.

# Claude Desktop 자신이 남기는 MCP 서버별 stdout/stderr 캡처 — Lens 홈 디렉터리 로그와는
# 별개다("이 도구가 왜 응답이 없었는지"는 보통 Lens 쪽이 아니라 여기 있다). mcp.log는
# 어느 서버가 응답 없는지 등 전체 그림 파악에 유용해 함께 담는다.
#
# 파일명은 설정 파일의 mcpServers 키를 그대로 따르는데, 그 키는 사용자가 정한다.
# 예전엔 우리가 쓸 법한 이름을 통째로 나열해뒀다 — 실제 문의(2026-08-13)에서 키를
# `stocklens-mcp`로 등록한 PC의 `mcp-server-stocklens-mcp.log`가 그 목록에 없어서
# 통째로 빠졌다. 번들엔 폴더 자체가 안 생기고 못 담았다는 메모도 안 남아(파일이 아예
# 없는 경우와 구분이 안 된다), 받아보는 쪽은 로그가 원래 없는 줄 안다.
#
# 그래서 이름을 정확히 맞히는 대신 Lens 이름이 들어간 mcp-server-*.log 를 모두 담는다.
# 남의 MCP 서버 로그(notion 등)까지 쓸어담지는 않는다 — 우리 문제와 무관하고, 그쪽
# 비밀까지 대신 내보낼 이유가 없다.
_CLAUDE_MCP_LOG_KEYWORDS = ("stocklens", "stock-data", "dartlens", "dart-mcp", "telegramlens")


def _claude_mcp_logs(folder: Path) -> list[Path]:
    """mcp.log + Lens 이름이 들어간 mcp-server-*.log 전부."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    out = []
    for f in entries:
        name = f.name.lower()
        if not name.endswith(".log"):
            continue
        if name == "mcp.log" or (
            name.startswith("mcp-server-")
            and any(k in name for k in _CLAUDE_MCP_LOG_KEYWORDS)
        ):
            out.append(f)
    return sorted(out)


def _claude_store_logs_dirs() -> list[Path]:
    """Microsoft Store 버전 Claude Desktop의 샌드박스 로그 폴더들.

    Store 앱은 %APPDATA% 를 패키지 안으로 리디렉션하므로 표준 경로에는 아무것도
    쌓이지 않는다. setup_claude 는 config 를 이미 이렇게 찾고 있었는데 로그만
    빠져 있었다 — 실제 문의(2026-08-13, 뉴질랜드 사용자)에서 번들에 Claude 로그가
    한 건도 안 담겨 고객에게 따로 압축을 부탁해야 했다. 패키지 해시는 사용자마다
    다를 수 있어 glob 으로 찾는다.
    """
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return []
    packages = Path(local_appdata) / "Packages"
    if not packages.is_dir():
        return []
    out: list[Path] = []
    for pattern in ("Claude_*", "*Claude*"):
        for pkg in sorted(packages.glob(pattern)):
            candidate = pkg / "LocalCache" / "Roaming" / "Claude" / "logs"
            if candidate.is_dir() and candidate not in out:
                out.append(candidate)
    return out


def _claude_desktop_logs_dir() -> Path:
    """Claude Desktop이 로그를 쌓는 폴더. Windows에서 실측 확인(%APPDATA%/Claude/logs) —
    macOS는 Electron 기본 관례(~/Library/Logs/<앱이름>)를 따른다고 가정한다(미검증).

    Windows에서는 표준 설치판과 Store 버전 경로가 동시에 남아 있을 수 있다(Store를
    지워도 폴더가 남는다). 그래서 존재 여부가 아니라 **실제로 MCP 로그가 들어 있는
    쪽**을 고른다 — 빈 잔재 폴더를 골라 "해당하는 파일 없음"을 적어 보내면 받아보는
    쪽이 또 헛다리를 짚는다.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "Claude"
    if sys.platform != "win32":
        return Path.home() / ".config" / "Claude" / "logs"

    appdata = os.environ.get("APPDATA")
    standard = (
        Path(appdata) / "Claude" / "logs" if appdata
        else Path.home() / "AppData" / "Roaming" / "Claude" / "logs"
    )
    candidates = [standard, *_claude_store_logs_dirs()]
    for folder in candidates:
        if _claude_mcp_logs(folder):
            return folder
    for folder in candidates:
        if folder.is_dir():
            return folder
    return standard


def _stocklens_logs_dir() -> Path:
    base = Path(os.environ.get("USERPROFILE") or str(Path.home()))
    return base / "Downloads" / "kstock" / "logs"


def _dartlens_home() -> Path:
    override = os.environ.get("DARTLENS_HOME")
    if override:
        return Path(override)
    primary = Path.home() / ".dartlens"
    legacy = Path.home() / ".dart-mcp-server"
    return legacy if (not primary.exists() and legacy.exists()) else primary


def _telegramlens_home() -> Path:
    override = os.environ.get("TELEGRAMLENS_HOME")
    return Path(override) if override else (Path.home() / ".telegramlens")


def _probe(label: str, folder: Path, notes: list[str]) -> bool:
    """이 폴더를 실제로 들여다볼 수 있는지. 못 보면 그 이유를 notes에 남긴다.

    macOS는 바탕화면·다운로드·문서 폴더를 권한(TCC)으로 막는다. 막히면 예전엔
    `.exists()`가 조용히 False를 돌려줘서, 로그가 한 줄도 없는 번들이 "정상"인 것처럼
    만들어졌다 — 받아보는 쪽에서는 문제가 없어서 로그가 없는 건지, 못 읽어서 없는
    건지 구분할 수가 없다. 구분해서 적어야 헛다리를 안 짚는다."""
    try:
        if not folder.exists():
            notes.append(f"- {label}: 폴더 없음 ({folder})")
            return False
        next(folder.iterdir(), None)  # 권한이 없으면 여기서 걸린다
        return True
    except PermissionError:
        notes.append(f"- {label}: 권한이 없어 못 읽음 ({folder})")
        return False
    except OSError as e:
        notes.append(f"- {label}: 읽지 못함 ({folder}) — {type(e).__name__}")
        return False


def _safe_files(notes: list[str] | None = None) -> list[tuple[str, Path]]:
    """(zip 안 상대경로, 실제 경로) — 존재하는 파일만. 라이선스·세션·자격증명 파일은
    여기 목록에 아예 없다(포함 여부를 실수로 뒤집을 수 없도록 allowlist 방식).

    `notes`를 주면 못 읽은 곳을 거기에 적는다(summary.txt에 실린다)."""
    notes = [] if notes is None else notes
    found: list[tuple[str, Path]] = []

    stocklens_logs = _stocklens_logs_dir()
    if _probe("StockLens 로그", stocklens_logs, notes):
        for f in sorted(stocklens_logs.glob("metrics_*.jsonl"))[-_RECENT_LOG_FILES:]:
            found.append((f"stocklens/{f.name}", f))

    dartlens_logs = _dartlens_home() / "logs"
    if _probe("DartLens 로그", dartlens_logs, notes):
        for f in sorted(dartlens_logs.glob("metrics_*.jsonl"))[-_RECENT_LOG_FILES:]:
            found.append((f"dartlens/{f.name}", f))

    tl_home = _telegramlens_home()
    if _probe("TelegramLens 상태", tl_home, notes):
        for name in ("daemon_status.json", "daemon.pid", "daemon.log", "daemon.log.1", "daemon.log.2"):
            f = tl_home / name
            if f.exists():
                found.append((f"telegramlens/{name}", f))

    claude_logs = _claude_desktop_logs_dir()
    if _probe("Claude Desktop 로그", claude_logs, notes):
        matched = _claude_mcp_logs(claude_logs)
        if not matched:
            # 한 건도 없으면 그 사실을 적는다 — 폴더는 읽었는데 파일이 없는 것과
            # 우리가 못 알아본 것은 받아보는 쪽에서 구분이 안 된다.
            notes.append(f"- Claude Desktop MCP 로그: 해당하는 파일 없음 ({claude_logs})")
        for f in matched:
            try:
                if f.stat().st_size <= _MAX_CLAUDE_LOG_BYTES:
                    found.append((f"claude-desktop-logs/{f.name}", f))
                else:
                    notes.append(f"- Claude Desktop 로그 {f.name}: 너무 커서 제외")
            except OSError:
                notes.append(f"- Claude Desktop 로그 {f.name}: 읽지 못함")

    return [(arc, p) for arc, p in found if p.is_file()]


def _recent_call_failures(files: list[tuple[str, Path]]) -> list[str]:
    """번들에 담기는 metrics_*.jsonl을 그대로 세어 Lens별 실패를 한 줄로 요약한다.

    각 줄은 `{"tool": ..., "error": "ConnectError"|null, ...}` 형태의 JSON 한 줄이다.
    형태가 다르거나 깨진 줄은 조용히 건너뛴다 — 요약 한 줄 만들려다 번들 생성 자체가
    실패하면 안 된다(도움을 요청할 방법을 잃는 쪽이 더 나쁘다).

    세는 것은 '기록된 error'뿐이다. StockLens의 search_stock처럼 예외를 삼키고 빈
    결과를 돌려주는 도구는 error가 null로 남아 여기 안 잡히지만, 그런 경우에도 같은
    시간대의 다른 도구가 실패로 남기 때문에 신호를 놓치지는 않는다.
    """
    tallies: dict[str, dict] = {}
    for arcname, path in files:
        lens_dir = arcname.split("/")[0]
        display = _LENS_DISPLAY_BY_DIR.get(lens_dir)
        if not display or "metrics_" not in arcname:
            continue
        t = tallies.setdefault(display, {"total": 0, "failed": 0, "kinds": {}, "last": None})
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            t["total"] += 1
            err = row.get("error")
            if not err:
                continue
            t["failed"] += 1
            t["kinds"][str(err)] = t["kinds"].get(str(err), 0) + 1
            t["last"] = (row.get("timestamp") or "?", row.get("tool") or "?")

    out: list[str] = []
    for display in (lens.display_name for lens in LENSES):
        t = tallies.get(display)
        if not t or not t["failed"]:
            continue
        kinds = ", ".join(
            f"{k} {v}건" for k, v in sorted(t["kinds"].items(), key=lambda kv: -kv[1])
        )
        stamp, tool = t["last"]
        out.append(f"- {display}: {t['total']}건 중 {t['failed']}건 실패 — {kinds} (마지막 {stamp} {tool})")
    return out


def _manager_version() -> str:
    """이 Manager의 버전. 못 읽어도 요약 생성을 막지 않는다 — 버전 한 줄 때문에
    지원 파일 자체가 안 만들어지면 도움을 요청할 방법을 잃는다."""
    try:
        from leetkit_manager import __version__

        return __version__
    except Exception:
        return "?"


def _summary_text(
    notes: list[str] | None = None, files: list[tuple[str, Path]] | None = None
) -> str:
    # Manager 자신의 버전을 맨 위에 적는다. 이게 없으면 받아보는 쪽이 "이 사람은 어떤
    # Manager를 쓰고 있나"를 물어봐야 하고, 그 왕복 한 번에 반나절이 간다 — 정작
    # 그 버전에서 이미 고쳐진 문제인 경우가 흔하다. Lens 버전은 아래에 다 적으면서
    # 정작 이 파일을 만든 프로그램의 버전만 빠져 있었다.
    lines = [
        f"LeetKit Manager 진단 요약 (Manager v{_manager_version()})",
        f"생성 시각: {datetime.now().isoformat(timespec='seconds')}",
        f"운영체제: {sys.platform}",
        "",
    ]

    # 번들에 담긴 호출 기록부터 센다 — 실사용에서 확인된 문제: 네이버/DART에 아예
    # 연결이 안 되는 PC가 보내온 번들의 summary.txt가 "정상 / 문제 없음"이었다.
    # 같은 zip 안 metrics_*.jsonl에는 ConnectError가 스무 건 넘게 쌓여 있었는데도
    # 요약이 그걸 안 읽었다. 요약과 로그가 서로 반대말을 하면 받아보는 쪽은 로그를
    # 한 줄씩 세기 전까지 헛다리를 짚는다. 이건 이미 기록된 사실이라 네트워크를
    # 새로 부르지 않고도 확실하다 — 그래서 온라인 진단보다 먼저 적는다.
    failures = _recent_call_failures(files or [])
    if failures:
        lines.append("최근 도구 호출 실패 (담긴 기록 기준)")
        lines.extend(failures)
        lines.append("")

    # online=True — 오프라인 진단은 "설치·라이선스·설정이 멀쩡한가"만 본다. 지원
    # 번들을 만드는 순간은 정의상 뭔가 안 되고 있는 때인데, 정작 "데이터를 가져올 수
    # 있는가"를 안 물어보면 아무 문제도 못 찾는다.
    for diag in orchestrator.run_full_diagnosis(LENSES, online=True, timeout=_DIAGNOSIS_TIMEOUT):
        version = diag.report.installed_version if diag.report else "?"
        lines.append(f"[{diag.lens.display_name}] v{version} — {diag.readiness}")
        if diag.report:
            problems = [c for c in diag.report.checks if c.status not in ("ok", "active", "skip", "info-skip")]
            in_progress = [c for c in diag.report.checks if c.status == "active"]
            if not problems:
                lines.append("  문제 없음")
            for c in problems:
                lines.append(f"  - [{c.id}] {c.summary}")
            for c in in_progress:
                lines.append(f"  - [진행중][{c.id}] {c.summary}")
        lines.append("")

    # 못 읽은 곳을 적어둔다 — 로그가 없는 게 "문제가 없어서"인지 "못 읽어서"인지
    # 구분이 안 되면 받아보는 쪽에서 헛다리를 짚는다(macOS 권한 차단이 특히 그렇다).
    if notes:
        lines.append("모으지 못한 것")
        lines.extend(notes)
        lines.append("")
    return redaction.redact("\n".join(lines))


def _desktop_dir() -> Path:
    """사용자에게 실제로 보이는 바탕화면.

    `Path.home()/"Desktop"` 이면 될 것 같지만 아니다 — 한국어 윈도우 + OneDrive 조합에서
    바탕화면은 `C:\\Users\\<name>\\OneDrive\\바탕 화면` 으로 옮겨가고, 예전 자리에는 빈
    `Desktop` 폴더가 껍데기로 남는다(실측: 그 폴더는 존재하고 쓰기도 된다). 그래서 저장은
    성공하는데 사용자 눈에는 아무것도 안 보인다 — 실제로 이 문제로 "파일이 안 생긴다"고
    한 번 헤맸다. 실제 위치는 레지스트리가 알고 있으니 그걸 묻는다.
    """
    if sys.platform == "win32":
        try:
            import winreg

            key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                path = Path(winreg.QueryValueEx(k, "Desktop")[0])
            if path.is_dir():
                return path
        except (OSError, ValueError, ImportError):
            pass  # 못 물어보면 아래 관례적 위치로 — 최악이라도 어딘가에는 저장된다
    return Path.home() / "Desktop"


def _writable_dest_dir() -> Path:
    """zip을 저장할 수 있는 첫 번째 폴더. 바탕화면 → 홈 → 임시 폴더 순.

    macOS는 바탕화면을 권한(TCC)으로 막는다 — 예전엔 거기서 막히면 예외가 그대로
    올라가 "번들을 만들지 못했습니다"로 끝났고, 정작 도움이 필요한 사람이 도움을
    요청할 방법을 잃었다. 어디든 만들어지는 쪽이 낫다(어디 저장됐는지는 화면에 띄운다).
    """
    import tempfile

    for candidate in (_desktop_dir(), Path.home(), Path(tempfile.gettempdir())):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".leetkit-write-test"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue
    return Path(tempfile.gettempdir())


def create_bundle(dest_dir: Path | None = None) -> Path:
    """안전 목록 파일 + 진단 요약을 zip으로 묶어 dest_dir(기본 바탕화면)에 저장. zip 경로 반환."""
    dest_dir = dest_dir or _writable_dest_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = dest_dir / f"leetkit-support-{stamp}.zip"

    # 파일을 먼저 모아야 못 읽은 곳(notes)이 채워지고, 그걸 summary.txt에 담을 수 있다.
    notes: list[str] = []
    files = _safe_files(notes)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("summary.txt", _summary_text(notes, files))
        for arcname, path in files:
            zf.writestr(arcname, _redacted_file_text(path))

    return zip_path


def _redacted_file_text(path: Path) -> str:
    """번들에 담기 전에 로그 본문도 마스킹한다.

    예전엔 summary.txt만 redact하고 로그 파일은 `zf.write()`로 원문 그대로 담았다 —
    확인된 유출 경로: DART API 호출이 HTTP 오류를 내면 httpx 예외 문자열에 쿼리스트링
    (`?crtfc_key=<40자리>`)이 통째로 들어가고, 그게 Claude Desktop의 MCP 서버 로그에
    남아 번들에 그대로 실려 나갔다. 그 외에도 로그엔 전화번호와 `C:\\Users\\<실명>`
    경로가 흔하다. 번들은 고객이 이메일로 밖에 보내는 물건이므로 원문으로 담으면 안 된다.

    바이너리이거나 읽을 수 없는 파일은 마스킹을 보장할 수 없으니 아예 제외한다
    (안전한 쪽으로 실패) — 지금 안전 목록은 전부 텍스트 로그/JSON이라 실제로는
    걸릴 일이 없다."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"(이 파일을 읽지 못해 번들에서 제외했습니다: {type(e).__name__})"
    return redaction.redact(raw)


def reveal_in_file_manager(path: Path) -> bool:
    """탐색기(Windows)/Finder(macOS)에서 파일이 바로 보이게 폴더를 연다. 열었으면 True.

    열렸는지를 돌려주는 이유: 예전엔 무조건 "폴더가 열렸습니다"라고 안내했는데,
    macOS에서 권한이나 환경 때문에 안 열려도 그대로 그렇게 말했다. 그러면 사용자는
    열리지도 않은 창을 찾는다. 못 열었으면 경로를 대신 알려줘야 한다."""
    try:
        if sys.platform == "win32":
            # 문자열로 넘긴다. 리스트로 주면 파이썬이 `/select,<경로>` **전체**를 한
            # 인자로 보고 통째로 따옴표를 씌우는데(`explorer "/select,C:\... 화면\a.zip"`),
            # 탐색기는 그걸 파싱하지 못하고 조용히 기본 폴더를 연다. 경로에 공백이
            # 없으면 우연히 동작해서 오래 안 걸렸다 — 한국어 윈도우 + OneDrive 조합의
            # 바탕화면이 `...\OneDrive\바탕 화면` 이라 거기서 처음 드러났다.
            # 사용자에게는 "폴더는 열렸는데 zip이 없다"로 보인다.
            # 따옴표는 경로에만 둘러야 한다.
            subprocess.run(f'explorer /select,"{path}"')
            # explorer는 성공해도 0이 아닌 값을 돌려주는 일이 있어 반환 코드를 안 본다.
            return True
        if sys.platform == "darwin":
            return subprocess.run(["open", "-R", str(path)], capture_output=True).returncode == 0
        return subprocess.run(["xdg-open", str(path.parent)], capture_output=True).returncode == 0
    except Exception:
        return False  # 못 열어도 zip 자체는 이미 만들어져 있으니 치명적이지 않음


def mail_compose_info(zip_path: Path, to_email: str = SUPPORT_EMAIL) -> dict:
    """고객이 어떤 메일 앱을 쓰든 그대로 복사해서 쓸 수 있는 받는사람/제목/본문."""
    subject = "LeetKit 문의 - 진단 로그 첨부"
    body = (
        "안녕하세요, LeetKit 제품 문의드립니다.\n\n"
        "[여기에 어떤 문제가 있었는지 적어주세요]\n\n"
        f"첨부파일: {zip_path.name}"
    )
    return {"to": to_email, "subject": subject, "body": body, "zip_path": str(zip_path)}

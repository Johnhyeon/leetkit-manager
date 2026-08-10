"""고객 지원 문의용 번들 — 로그·상태 파일만 골라 압축한다.

전체 홈 디렉터리를 통째로 압축하지 않는다 — StockLens/DartLens의 `license.key`,
TelegramLens의 `session.session`/`credentials.json`처럼 절대 밖으로 나가면 안 되는
파일이 로그와 같은 폴더에 섞여 있기 때문이다. 여기서는 안전 목록(로그·상태 JSON만)에
있는 파일만 골라 담고, summary.txt(세 Lens 진단 요약, redaction 적용)를 함께 넣는다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from leetkit_manager import orchestrator, redaction
from leetkit_manager.lens_contract import LENSES

SUPPORT_EMAIL = "osy980315@gmail.com"

_RECENT_LOG_FILES = 7  # 로그 파일이 날짜별로 쌓이는 Lens는 최근 N개만 담는다.
_MAX_CLAUDE_LOG_BYTES = 20 * 1024 * 1024  # Claude 쪽 로그는 회전 없이 계속 자라므로 안전 상한.

# Claude Desktop 자신이 남기는 MCP 서버별 stdout/stderr 캡처 — Lens 홈 디렉터리 로그와는
# 별개다("이 도구가 왜 응답이 없었는지"는 보통 Lens 쪽이 아니라 여기 있다). 파일명이 설정
# 파일의 mcpServers 키를 그대로 따르므로, 이름이 바뀐 legacy 키(dart-mcp, stock-data 등)도
# 같이 챙긴다. mcp.log는 어느 서버가 응답 없는지 등 전체 그림 파악에 유용해 함께 담는다.
_CLAUDE_MCP_LOG_NAMES = (
    "mcp.log",
    "mcp-server-stocklens.log",
    "mcp-server-stock-data.log",
    "mcp-server-stocklens-report.log",
    "mcp-server-dartlens.log",
    "mcp-server-dart-mcp.log",
    "mcp-server-telegramlens.log",
)


def _claude_desktop_logs_dir() -> Path:
    """Claude Desktop이 로그를 쌓는 폴더. Windows에서 실측 확인(%APPDATA%/Claude/logs) —
    macOS는 Electron 기본 관례(~/Library/Logs/<앱이름>)를 따른다고 가정한다(미검증)."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "Claude" / "logs" if appdata else Path.home() / "AppData" / "Roaming" / "Claude" / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "Claude"
    return Path.home() / ".config" / "Claude" / "logs"


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
        for name in _CLAUDE_MCP_LOG_NAMES:
            f = claude_logs / name
            try:
                if f.is_file() and f.stat().st_size <= _MAX_CLAUDE_LOG_BYTES:
                    found.append((f"claude-desktop-logs/{name}", f))
            except OSError:
                notes.append(f"- Claude Desktop 로그 {name}: 읽지 못함")

    return [(arc, p) for arc, p in found if p.is_file()]


def _summary_text(notes: list[str] | None = None) -> str:
    lines = [
        "LeetKit Manager 진단 요약",
        f"생성 시각: {datetime.now().isoformat(timespec='seconds')}",
        f"운영체제: {sys.platform}",
        "",
    ]
    for lens in LENSES:
        diag = orchestrator.diagnose_lens(lens)
        version = diag.report.installed_version if diag.report else "?"
        lines.append(f"[{lens.display_name}] v{version} — {diag.readiness}")
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


def _writable_dest_dir() -> Path:
    """zip을 저장할 수 있는 첫 번째 폴더. 바탕화면 → 홈 → 임시 폴더 순.

    macOS는 바탕화면을 권한(TCC)으로 막는다 — 예전엔 거기서 막히면 예외가 그대로
    올라가 "번들을 만들지 못했습니다"로 끝났고, 정작 도움이 필요한 사람이 도움을
    요청할 방법을 잃었다. 어디든 만들어지는 쪽이 낫다(어디 저장됐는지는 화면에 띄운다).
    """
    import tempfile

    for candidate in (Path.home() / "Desktop", Path.home(), Path(tempfile.gettempdir())):
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
        zf.writestr("summary.txt", _summary_text(notes))
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
            # explorer는 성공해도 0이 아닌 값을 돌려주는 일이 있어 반환 코드를 안 본다.
            subprocess.run(["explorer", f"/select,{path}"])
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

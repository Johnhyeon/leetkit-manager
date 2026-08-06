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


def _safe_files() -> list[tuple[str, Path]]:
    """(zip 안 상대경로, 실제 경로) — 존재하는 파일만. 라이선스·세션·자격증명 파일은
    여기 목록에 아예 없다(포함 여부를 실수로 뒤집을 수 없도록 allowlist 방식)."""
    found: list[tuple[str, Path]] = []

    stocklens_logs = _stocklens_logs_dir()
    if stocklens_logs.exists():
        for f in sorted(stocklens_logs.glob("metrics_*.jsonl"))[-_RECENT_LOG_FILES:]:
            found.append((f"stocklens/{f.name}", f))

    dartlens_logs = _dartlens_home() / "logs"
    if dartlens_logs.exists():
        for f in sorted(dartlens_logs.glob("metrics_*.jsonl"))[-_RECENT_LOG_FILES:]:
            found.append((f"dartlens/{f.name}", f))

    tl_home = _telegramlens_home()
    for name in ("daemon_status.json", "daemon.pid", "daemon.log", "daemon.log.1", "daemon.log.2"):
        f = tl_home / name
        if f.exists():
            found.append((f"telegramlens/{name}", f))

    claude_logs = _claude_desktop_logs_dir()
    if claude_logs.exists():
        for name in _CLAUDE_MCP_LOG_NAMES:
            f = claude_logs / name
            if f.exists() and f.stat().st_size <= _MAX_CLAUDE_LOG_BYTES:
                found.append((f"claude-desktop-logs/{name}", f))

    return [(arc, p) for arc, p in found if p.is_file()]


def _summary_text() -> str:
    lines = ["LeetKit Manager 진단 요약", f"생성 시각: {datetime.now().isoformat(timespec='seconds')}", ""]
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
    return redaction.redact("\n".join(lines))


def create_bundle(dest_dir: Path | None = None) -> Path:
    """안전 목록 파일 + 진단 요약을 zip으로 묶어 dest_dir(기본 바탕화면)에 저장. zip 경로 반환."""
    dest_dir = dest_dir or (Path.home() / "Desktop")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = dest_dir / f"leetkit-support-{stamp}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("summary.txt", _summary_text())
        for arcname, path in _safe_files():
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


def reveal_in_file_manager(path: Path) -> None:
    """탐색기(Windows)/Finder(macOS)에서 파일이 바로 보이게 폴더를 연다."""
    try:
        if sys.platform == "win32":
            subprocess.run(["explorer", f"/select,{path}"])
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)])
        else:
            subprocess.run(["xdg-open", str(path.parent)])
    except Exception:
        pass  # 탐색기를 못 열어도 zip 자체는 이미 만들어져 있으니 치명적이지 않음


def mail_compose_info(zip_path: Path, to_email: str = SUPPORT_EMAIL) -> dict:
    """고객이 어떤 메일 앱을 쓰든 그대로 복사해서 쓸 수 있는 받는사람/제목/본문."""
    subject = "LeetKit 문의 - 진단 로그 첨부"
    body = (
        "안녕하세요, LeetKit 제품 문의드립니다.\n\n"
        "[여기에 어떤 문제가 있었는지 적어주세요]\n\n"
        f"첨부파일: 방금 열린 폴더의 {zip_path.name} 을(를) 첨부해 주세요."
    )
    return {"to": to_email, "subject": subject, "body": body, "zip_path": str(zip_path)}

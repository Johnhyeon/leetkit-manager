"""온보딩 마법사(및 전체 GUI)를 "방금 산 컴퓨터" 상태로 눈으로 확인하기 위한 개발용 실행기.

새 Windows 계정이나 VM을 따로 만드는 대신, HOME만 임시 폴더로 돌려서 격리한다
(USERPROFILE/HOME/APPDATA 환경변수) — `~/.leetkit-manager`, webview localStorage,
`~/.claude.json`/`%APPDATA%/Claude/...`/`~/.codex/config.toml` 전부 이 프로세스 안에서만
격리된 새 경로를 보고, 실제 계정의 어떤 파일도 안 건드린다.

주의: 이 격리는 파일 기반 경로에만 적용된다. DartLens의 DART API 키처럼 OS 키체인
(Windows Credential Manager 등, `keyring` 라이브러리)에 저장되는 자격증명은 HOME과
무관하게 Windows 로그인 계정 단위로 공유된다 — 이 데모에서 API 키 등록을 실제로
끝까지 진행하면 실제 프로덕션 키를 덮어쓸 수 있다는 뜻이다. 라이선스 키는 파일
기반이라 격리되지만, API 키 등록 단계는 이 사실을 알고(가짜 키만 넣어 실패 경로만
확인하거나, 실제 키로 덮어써도 상관없다고 판단한 경우에만) 진행할 것.

Lens 설치는 PyPI가 아니라 이 리포의 형제 디렉터리(D:/project/stocklens/mcp,
mcp-dart, telegramlens)에서 직접 uv tool install한다 — 지금 세션에서 만든
Codex 타겟·텔레그램 로그인 스테퍼 같은 기능은 아직 PyPI에 배포 전이라, PyPI 최신
버전만 받으면 이 기능들이 전혀 동작하지 않는 걸 그대로 보게 된다(실제로 처음
버전에서 "호환되지 않는 Lens 버전"/"setup 응답을 파싱할 수 없습니다 (exit=2)"로
드러난 문제 — Manager 버그가 아니라 Lens 쪽 배포 지연이었다). 로컬 소스로 설치하면
지금 작업 중인 코드까지 포함해서 실제 배포될 물건과 동일하게 검증할 수 있다.

- 라이선스/DART API 키 입력은 실제 activate/setup 바이너리가 검증한다 — 가짜 키를 넣으면
  실제 실패 메시지가, 진짜 보유한 키를 넣으면 실제 성공이 뜬다.
- 텔레그램 로그인도 실제 `telegramlens-login --stepper`가 뜬다 — my.telegram.org에서 받은
  진짜 API_ID/HASH와 실제 전화번호·SMS 코드가 있어야 완주된다(없으면 중간에 취소하고
  화면만 확인하면 됨).

uv가 이 컴퓨터에 없으면 설치 과정에서 공식 설치 스크립트로 자동 부트스트랩된다(실제
신규 구매자와 동일한 경로 — package_service.ensure_uv_available 참고).

실행: `python dev/demo_stub.py` (leetkit-manager 리포 루트에서)
배포용 leetkit-manager 패키지에는 포함되지 않는다(pyproject.toml의 wheel 대상이
`leetkit_manager` 패키지 하나뿐이므로 dev/ 는 자동으로 제외됨).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

_DEMO_HOME = tempfile.mkdtemp(prefix="leetkit-demo-home-")
os.environ["USERPROFILE"] = _DEMO_HOME
os.environ["HOME"] = _DEMO_HOME
os.environ["APPDATA"] = os.path.join(_DEMO_HOME, "AppData", "Roaming")
os.makedirs(os.environ["APPDATA"], exist_ok=True)
print(f"[demo_stub] 격리된 가짜 HOME: {_DEMO_HOME}")
print("[demo_stub] uv 설치/라이선스 검증은 전부 실제로 동작합니다 — 네트워크 필요, 설치에 시간이 걸립니다.")
print("[demo_stub] 주의: DartLens DART API 키는 OS 키체인에 저장되어 HOME 격리를 안 받습니다 (실제 계정과 공유).")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_REPO_ROOT = Path(__file__).parent.parent
_LOCAL_LENS_PATHS = {
    "stocklens-mcp": _REPO_ROOT.parent / "mcp",
    "dartlens-mcp": _REPO_ROOT.parent / "mcp-dart",
    "telegramlens-mcp": _REPO_ROOT.parent / "telegramlens",
}


def _install_from_local_repo(package_name: str, _version: str, *, timeout=None):
    """package_service.install_version 대신 — PyPI가 아니라 형제 리포 소스에서 설치해
    이 세션에서 아직 배포 안 한 변경사항(Codex 타겟, 텔레그램 로그인 스테퍼 등)까지
    포함해서 검증한다. _version은 로컬 설치엔 의미가 없어 무시(실제 버전은 각 리포
    pyproject.toml이 결정)."""
    from leetkit_manager import package_service

    local_path = _LOCAL_LENS_PATHS.get(package_name)
    if local_path is None or not local_path.exists():
        return package_service.install_version(package_name, _version, timeout=timeout or 120.0)
    return package_service.run_cli(
        ["uv", "tool", "install", "--force", str(local_path)],
        timeout=timeout or 120.0,
    )


def main() -> None:
    from leetkit_manager import package_service
    from leetkit_manager.ui import app

    with patch.object(package_service, "install_version", side_effect=_install_from_local_repo):
        app.run()


if __name__ == "__main__":
    main()

"""Claude Desktop/Code 설정 파일 백업·복원.

각 Lens의 `<lens>-setup`도 자기 몫의 백업(`.json.backup`, 고정 이름 — 매번 덮어씀)을
따로 만들지만, 그건 그 Lens 한 번의 변경만 되돌릴 수 있다. 이 모듈은 "여러 Lens를
순차 설치·업데이트하는 배치 작업을 시작하는 시점"의 통짜 스냅샷을 시각이 포함된
이름(`.bak-YYYYMMDD-HHMMSS`)으로 남겨, 배치 중간에 무엇이 잘못돼도 최초 상태로
되돌릴 수 있게 하는 바깥쪽 안전망이다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def backup_path_for(config_path: Path, *, at: datetime | None = None) -> Path:
    at = at or datetime.now()
    stamp = at.strftime("%Y%m%d-%H%M%S")
    return config_path.with_name(config_path.name + f".bak-{stamp}")


def backup_config(config_path: Path, *, at: datetime | None = None) -> Path | None:
    """config_path가 존재하면 타임스탬프 백업을 만들고 그 경로를 반환한다.

    파일이 아직 없으면(해당 클라이언트를 안 쓰는 경우) 백업할 것이 없으므로 None —
    "백업 없이 설정을 덮어쓰지 않는다"는 새 파일 생성까지 막는 규칙이 아니라, 기존
    내용을 잃을 위험이 있을 때만 적용된다.
    """
    if not config_path.exists():
        return None
    backup = backup_path_for(config_path, at=at)
    backup.write_bytes(config_path.read_bytes())
    return backup


def restore_config(config_path: Path, backup_path: Path) -> bool:
    """백업 내용을 원래 경로에 되돌려 쓴다. 백업이 없으면 아무 것도 하지 않고 False —
    Manager는 장애 복구를 이유로 파일을 임의로 만들거나 지우지 않는다."""
    if not backup_path.exists():
        return False
    config_path.write_bytes(backup_path.read_bytes())
    return True

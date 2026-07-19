from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PathBoundaryError(ValueError):
    """Raised when a runtime path violates the local-state boundary."""


def _looks_like_network_path(path: Path) -> bool:
    raw = str(path)
    if raw.startswith(("\\\\", "//")):
        return True
    drive = path.drive
    return bool(os.name == "nt" and drive.startswith("\\\\"))


def _reject_existing_symlink_components(path: Path, *, label: str) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise PathBoundaryError(f"{label} 路径不能经过符号链接: {current}")
        parent = current.parent
        if parent == current:
            break
        current = parent


def _ensure_directory(path: Path, *, label: str, require_local: bool = False) -> None:
    _reject_existing_symlink_components(path, label=label)
    if require_local and _looks_like_network_path(path):
        raise PathBoundaryError(f"{label} 必须位于本机磁盘，不能使用 UNC/网络共享: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise PathBoundaryError(f"{label} 不是目录: {path}")


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path
    config_dir: Path
    userdata_dir: Path
    records_dir: Path
    database_path: Path

    @classmethod
    def defaults(cls, root: Path = ROOT) -> RuntimePaths:
        root = root.resolve()
        userdata = root / "userdata"
        return cls(
            root=root,
            config_dir=root / "config",
            userdata_dir=userdata,
            records_dir=root / "records",
            database_path=userdata / "douyin_recorder.db",
        )

    def ensure(self) -> None:
        _ensure_directory(self.config_dir, label="config")
        _ensure_directory(self.userdata_dir, label="userdata", require_local=True)
        _ensure_directory(self.records_dir, label="records")
        _reject_existing_symlink_components(self.database_path, label="SQLite 数据库")
        try:
            self.database_path.relative_to(self.userdata_dir)
        except ValueError as exc:
            raise PathBoundaryError(
                "SQLite 数据库必须位于 userdata 目录内，避免与媒体或网络共享混放"
            ) from exc

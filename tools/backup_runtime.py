from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.settings import Settings  # noqa: E402

_MAX_CONFIG_BYTES = 2 * 1024 * 1024
_MAX_RECORD_ROWS = 100_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_regular_file(source: Path, target: Path) -> bool:
    if not source.exists():
        return False
    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f"拒绝备份非普通文件或符号链接: {source}")
    if source.stat().st_size > _MAX_CONFIG_BYTES:
        raise RuntimeError(f"配置文件超过 {_MAX_CONFIG_BYTES} 字节上限: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def _backup_sqlite(source: Path, target: Path) -> dict[str, object]:
    if not source.exists():
        return {"present": False}
    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f"SQLite 路径类型不安全: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with (
        sqlite3.connect(source_uri, uri=True, timeout=10.0) as src,
        sqlite3.connect(target) as dst,
    ):
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()
        integrity = str(result[0]) if result else "missing"
    if integrity.lower() != "ok":
        raise RuntimeError(f"SQLite 备份完整性检查失败: {integrity}")
    return {
        "present": True,
        "filename": target.name,
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "integrity_check": integrity,
    }


def _records_manifest(records_dir: Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    total_bytes = 0
    if not records_dir.exists():
        return {"file_count": 0, "total_bytes": 0, "files": []}
    for path in sorted(records_dir.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"records 中发现符号链接，拒绝继续: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(records_dir).as_posix()
        stat = path.stat()
        rows.append(
            {
                "path": relative,
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
        total_bytes += stat.st_size
        if len(rows) > _MAX_RECORD_ROWS:
            raise RuntimeError(f"records 文件数超过 {_MAX_RECORD_ROWS}，请分批备份")
    return {"file_count": len(rows), "total_bytes": total_bytes, "files": rows}


def create_runtime_backup(
    output_dir: Path, *, settings: Settings | None = None
) -> dict[str, object]:
    settings = settings or Settings.load()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = output_dir / f"douyin-recorder-runtime-{stamp}.zip"

    with tempfile.TemporaryDirectory(prefix="douyin-runtime-backup-") as temp:
        stage = Path(temp) / "runtime"
        config_rows: list[str] = []
        for name in ("config.json", "runtime.env"):
            source = settings.paths.config_dir / name
            if _copy_regular_file(source, stage / "config" / name):
                config_rows.append(name)

        sqlite_info = _backup_sqlite(
            settings.paths.database_path,
            stage / "userdata" / settings.paths.database_path.name,
        )
        records = _records_manifest(settings.paths.records_dir)
        (stage / "records-manifest.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        metadata = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "config_files": config_rows,
            "sqlite": sqlite_info,
            "records_manifest": {
                "file_count": records["file_count"],
                "total_bytes": records["total_bytes"],
            },
            "warning": "该备份可能含私人配置；不得提交 GitHub，需在受保护位置保存。",
        }
        (stage / "backup-manifest.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    handle.write(path, path.relative_to(stage).as_posix())

    result = {
        "archive": str(archive),
        "bytes": archive.stat().st_size,
        "sha256": _sha256(archive),
        "config_files": config_rows,
        "sqlite_present": bool(sqlite_info.get("present")),
        "records_file_count": records["file_count"],
        "records_total_bytes": records["total_bytes"],
    }
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{result['sha256']}  {archive.name}\n", encoding="utf-8")
    result["checksum"] = str(checksum)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="一致性备份配置、SQLite 与录像索引")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "backups" / "runtime")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = create_runtime_backup(args.output_dir)
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"[失败] 运行数据备份失败: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

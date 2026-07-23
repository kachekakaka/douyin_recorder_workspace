from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.core import Database  # noqa: E402
from app.paths import RuntimePaths  # noqa: E402
from app.settings import Settings  # noqa: E402
from tools.backup_runtime import create_runtime_backup  # noqa: E402
from tools.database_integrity_check import check_database  # noqa: E402

_MARKER_ROOM_KEY = "recovery-smoke-room"
_MARKER_ROOM_URL = "https://live.douyin.com/73504089679"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: Path, destination: Path) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    members: list[str] = []
    with zipfile.ZipFile(archive) as handle:
        for info in handle.infolist():
            normalized = info.filename.replace("\\", "/")
            if normalized.startswith("/") or normalized.startswith("../"):
                raise RuntimeError("backup contains an unsafe member path")
            target = (destination / normalized).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError("backup member escapes the restore directory")
            mode = info.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise RuntimeError("backup contains a symbolic-link member")
            members.append(normalized)
        handle.extractall(destination)
    return sorted(name for name in members if name and not name.endswith("/"))


async def _create_source_database(path: Path) -> int:
    database = Database(path)
    await database.initialize()
    try:
        await database.execute(
            "INSERT INTO rooms(room_key, room_url, enabled, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, 1, 1, 1)",
            (_MARKER_ROOM_KEY, _MARKER_ROOM_URL),
        )
        return await database.schema_version()
    finally:
        await database.close()


def _marker_count(path: Path) -> int:
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM rooms WHERE room_key = ? AND room_url = ?",
            (_MARKER_ROOM_KEY, _MARKER_ROOM_URL),
        ).fetchone()
    return int(row[0]) if row else 0


def _build_settings(runtime_root: Path) -> Settings:
    paths = RuntimePaths(
        root=runtime_root,
        config_dir=runtime_root / "config",
        userdata_dir=runtime_root / "userdata",
        records_dir=runtime_root / "records",
        database_path=runtime_root / "userdata" / "douyin_recorder.db",
    )
    return Settings.load(root=ROOT, paths=paths, environ={})


def run_backup_restore_smoke(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists() and output_dir.is_symlink():
        raise RuntimeError("output directory must not be a symbolic link")
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="douyin-backup-restore-smoke-") as temp:
        temp_root = Path(temp)
        runtime_root = temp_root / "runtime"
        settings = _build_settings(runtime_root)
        source_schema = asyncio.run(_create_source_database(settings.paths.database_path))
        (settings.paths.records_dir / "sample.mkv").write_bytes(b"synthetic-media-index-only")

        backup_result = create_runtime_backup(output_dir, settings=settings)
        archive = Path(str(backup_result["archive"]))
        checksum = Path(str(backup_result["checksum"]))
        checksum_text = checksum.read_text(encoding="utf-8").strip()
        expected_checksum = checksum_text.split(maxsplit=1)[0]
        archive_checksum = _sha256(archive)
        checksum_matches = expected_checksum == archive_checksum

        restore_root = temp_root / "restored"
        members = _safe_extract(archive, restore_root)
        restored_database = restore_root / "userdata" / settings.paths.database_path.name
        integrity = check_database(restored_database)
        marker_count = _marker_count(restored_database) if integrity["passed"] else 0
        manifest = json.loads(
            (restore_root / "backup-manifest.json").read_text(encoding="utf-8")
        )

        passed = bool(
            checksum_matches
            and integrity["passed"]
            and integrity["migration_history"]["schema_version"] == source_schema
            and marker_count == 1
            and manifest.get("sqlite", {}).get("integrity_check") == "ok"
            and manifest.get("records_manifest", {}).get("file_count") == 1
        )
        report: dict[str, Any] = {
            "smoke_version": 1,
            "passed": passed,
            "archive": {
                "filename": archive.name,
                "bytes": archive.stat().st_size,
                "sha256": archive_checksum,
                "checksum_matches": checksum_matches,
                "member_count": len(members),
            },
            "database": {
                "source_schema_version": source_schema,
                "restored_schema_version": integrity["migration_history"]["schema_version"],
                "integrity_check": integrity["integrity_check"],
                "foreign_key_violation_count": integrity["foreign_key_violation_count"],
                "migration_history_passed": integrity["migration_history"]["passed"],
                "marker_row_count": marker_count,
            },
            "records_manifest_file_count": manifest.get("records_manifest", {}).get(
                "file_count"
            ),
            "redaction_policy": {
                "private_configuration_values": "excluded",
                "absolute_runtime_paths": "excluded",
                "media_contents": "not_archived",
            },
        }
        if not passed:
            report["error_code"] = "backup_restore_validation_failed"
        return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, extract, and validate an isolated runtime backup."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--json-output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run_backup_restore_smoke(args.output_dir)
    except (OSError, RuntimeError, sqlite3.Error, ValueError, zipfile.BadZipFile) as exc:
        print(f"[失败] 备份恢复演练失败: {type(exc).__name__}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_output is None:
        print(rendered, end="")
    else:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered, encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

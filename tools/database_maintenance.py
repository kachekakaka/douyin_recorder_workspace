from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.settings import Settings  # noqa: E402
from tools.backup_runtime import create_runtime_backup  # noqa: E402
from tools.database_integrity_check import check_database  # noqa: E402

BackupCreator = Callable[[Path], dict[str, object]]
_MIN_VACUUM_FREE_BYTES = 64 * 1024 * 1024
_MIN_VACUUM_FREE_RATIO = 0.20


class DatabaseMaintenanceError(RuntimeError):
    """Raised when safe maintenance preconditions are not met."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_metrics(path: Path) -> dict[str, object]:
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as connection:
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
    free_bytes = page_size * freelist_count
    total_bytes = page_size * page_count
    free_ratio = (free_bytes / total_bytes) if total_bytes else 0.0
    return {
        "database_bytes": path.stat().st_size,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "free_bytes": free_bytes,
        "free_ratio": round(free_ratio, 6),
        "journal_mode": journal_mode,
        "wal": _sidecar_summary(path.with_name(path.name + "-wal")),
        "shm": _sidecar_summary(path.with_name(path.name + "-shm")),
        "vacuum_recommended": bool(
            free_bytes >= _MIN_VACUUM_FREE_BYTES and free_ratio >= _MIN_VACUUM_FREE_RATIO
        ),
    }


def _sidecar_summary(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"present": False, "bytes": 0}
    if path.is_symlink() or not path.is_file():
        return {"present": True, "safe_type": False, "bytes": None}
    return {"present": True, "safe_type": True, "bytes": path.stat().st_size}


def _verify_backup(result: dict[str, object]) -> dict[str, object]:
    archive_value = result.get("archive")
    checksum_value = result.get("checksum")
    if not isinstance(archive_value, str) or not isinstance(checksum_value, str):
        raise DatabaseMaintenanceError("backup result is missing archive metadata")
    archive = Path(archive_value)
    checksum = Path(checksum_value)
    for path in (archive, checksum):
        if path.is_symlink() or not path.is_file():
            raise DatabaseMaintenanceError("backup output is not a regular file")
    line = checksum.read_text(encoding="utf-8").strip()
    parts = line.split(maxsplit=1)
    if len(parts) != 2 or parts[1].lstrip("*") != archive.name:
        raise DatabaseMaintenanceError("backup checksum sidecar is malformed")
    actual = _sha256(archive)
    if parts[0].lower() != actual:
        raise DatabaseMaintenanceError("backup checksum verification failed")
    return {
        "archive_filename": archive.name,
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": actual,
        "checksum_filename": checksum.name,
        "verified": True,
    }


def _same_migration_state(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_history = before.get("migration_history", {})
    after_history = after.get("migration_history", {})
    keys = (
        "schema_version",
        "expected_schema_version",
        "applied_migration_count",
        "unknown_versions",
        "missing_versions",
        "mismatches",
    )
    return all(before_history.get(key) == after_history.get(key) for key in keys)


def inspect_database_maintenance(path: Path) -> dict[str, Any]:
    path = path.resolve()
    validation = check_database(path)
    report: dict[str, Any] = {
        "maintenance_version": 1,
        "mode": "plan",
        "database_filename": path.name,
        "validation": validation,
        "actions": ["wal_checkpoint_truncate", "pragma_optimize"],
        "vacuum": {
            "execution_supported": False,
            "reason": "preflight_only_in_v0.1.1_phase2",
        },
        "passed": bool(validation.get("passed")),
    }
    if validation.get("passed"):
        try:
            metrics = _database_metrics(path)
        except sqlite3.Error:
            report["passed"] = False
            report["error_code"] = "sqlite_read_failed"
        else:
            report["metrics"] = metrics
            report["vacuum"]["recommended"] = metrics["vacuum_recommended"]
    else:
        report["error_code"] = "database_precheck_failed"
    return report


def run_database_maintenance(
    path: Path,
    *,
    apply: bool = False,
    confirm_stopped: bool = False,
    backup_dir: Path | None = None,
    backup_creator: BackupCreator | None = None,
    busy_timeout_ms: int = 1_000,
) -> dict[str, Any]:
    if not 100 <= busy_timeout_ms <= 60_000:
        raise ValueError("busy_timeout_ms must be between 100 and 60000")
    plan = inspect_database_maintenance(path)
    if not apply:
        return plan
    if not confirm_stopped:
        raise DatabaseMaintenanceError("--confirm-stopped is required for apply mode")
    if not plan.get("passed"):
        raise DatabaseMaintenanceError("database precheck failed")
    if backup_dir is None or backup_creator is None:
        raise DatabaseMaintenanceError("verified runtime backup is required")

    backup = _verify_backup(backup_creator(backup_dir.resolve()))
    before_validation = plan["validation"]
    before_metrics = plan["metrics"]
    checkpoint: tuple[int, int, int] | None = None
    optimized = False
    path = path.resolve()
    try:
        with closing(
            sqlite3.connect(path, timeout=busy_timeout_ms / 1000, isolation_level=None)
        ) as connection:
            connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            connection.execute("PRAGMA locking_mode=EXCLUSIVE")
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute("COMMIT")
            row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            checkpoint = tuple(int(item) for item in row) if row else (0, 0, 0)
            if checkpoint[0] != 0:
                raise DatabaseMaintenanceError("WAL checkpoint reported busy readers")
            connection.execute("PRAGMA optimize")
            optimized = True
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).casefold() or "busy" in str(exc).casefold():
            raise DatabaseMaintenanceError("database is busy; stop the application first") from exc
        raise DatabaseMaintenanceError("SQLite maintenance operation failed") from exc

    after_validation = check_database(path)
    if not after_validation.get("passed"):
        raise DatabaseMaintenanceError("database postcheck failed; preserve the verified backup")
    if not _same_migration_state(before_validation, after_validation):
        raise DatabaseMaintenanceError("migration state changed during maintenance")
    after_metrics = _database_metrics(path)
    return {
        "maintenance_version": 1,
        "mode": "apply",
        "database_filename": path.name,
        "backup": backup,
        "checkpoint": {
            "busy": checkpoint[0] if checkpoint else None,
            "log_frames": checkpoint[1] if checkpoint else None,
            "checkpointed_frames": checkpoint[2] if checkpoint else None,
        },
        "optimize_executed": optimized,
        "before": {"validation": before_validation, "metrics": before_metrics},
        "after": {"validation": after_validation, "metrics": after_metrics},
        "migration_state_unchanged": True,
        "vacuum": {
            "execution_supported": False,
            "recommended": after_metrics["vacuum_recommended"],
            "reason": "preflight_only_in_v0.1.1_phase2",
        },
        "passed": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or apply safe SQLite checkpoint and optimize maintenance."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-stopped", action="store_true")
    parser.add_argument("--busy-timeout-ms", type=int, default=1_000)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        root = args.root.resolve()
        settings = Settings.load(root=root)
        database = (args.database or settings.paths.database_path).resolve()
        backup_creator: BackupCreator | None = None
        if args.apply:
            if database != settings.paths.database_path.resolve():
                raise DatabaseMaintenanceError(
                    "apply mode only accepts the configured runtime database"
                )
            if args.backup_dir is None:
                raise DatabaseMaintenanceError("--backup-dir is required in apply mode")
            backup_creator = lambda output: create_runtime_backup(output, settings=settings)
        report = run_database_maintenance(
            database,
            apply=args.apply,
            confirm_stopped=args.confirm_stopped,
            backup_dir=args.backup_dir,
            backup_creator=backup_creator,
            busy_timeout_ms=args.busy_timeout_ms,
        )
    except (DatabaseMaintenanceError, OSError, sqlite3.Error, ValueError) as exc:
        report = {
            "maintenance_version": 1,
            "mode": "apply" if args.apply else "plan",
            "passed": False,
            "error_code": type(exc).__name__,
        }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())

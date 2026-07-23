from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Migration(Protocol):
    version: int
    name: str

    @property
    def checksum(self) -> str:
        ...


def _load_migrations() -> tuple[_Migration, ...]:
    path = ROOT / "app" / "db" / "migrations" / "__init__.py"
    spec = importlib.util.spec_from_file_location("_douyin_migrations", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load migration definitions")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    migrations = getattr(module, "MIGRATIONS", None)
    if not isinstance(migrations, tuple) or not migrations:
        raise RuntimeError("migration definitions are missing")
    return migrations


MIGRATIONS = _load_migrations()


def _readonly_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _migration_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if table is None:
        return []
    return list(
        connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
    )


def _migration_status(
    rows: list[sqlite3.Row], expected: tuple[_Migration, ...]
) -> dict[str, Any]:
    expected_by_version = {migration.version: migration for migration in expected}
    applied_versions = [int(row["version"]) for row in rows]
    unknown_versions = sorted(set(applied_versions) - set(expected_by_version))
    missing_versions = sorted(set(expected_by_version) - set(applied_versions))
    mismatches: list[dict[str, object]] = []
    for row in rows:
        version = int(row["version"])
        migration = expected_by_version.get(version)
        if migration is None:
            continue
        actual_name = str(row["name"])
        actual_checksum = str(row["checksum"])
        if actual_name != migration.name or actual_checksum != migration.checksum:
            mismatches.append(
                {
                    "version": version,
                    "name_matches": actual_name == migration.name,
                    "checksum_matches": actual_checksum == migration.checksum,
                }
            )
    latest = max(applied_versions, default=0)
    expected_latest = max(expected_by_version, default=0)
    return {
        "schema_version": latest,
        "expected_schema_version": expected_latest,
        "applied_migration_count": len(rows),
        "unknown_versions": unknown_versions,
        "missing_versions": missing_versions,
        "mismatches": mismatches,
        "passed": not unknown_versions and not missing_versions and not mismatches,
    }


def check_database(path: Path) -> dict[str, Any]:
    path = path.resolve()
    base: dict[str, Any] = {
        "database_filename": path.name,
        "exists": path.exists(),
        "is_regular_file": path.is_file() and not path.is_symlink(),
        "integrity_check": "not_run",
        "foreign_key_violation_count": None,
        "migration_history": {
            "schema_version": None,
            "expected_schema_version": max(migration.version for migration in MIGRATIONS),
            "applied_migration_count": 0,
            "unknown_versions": [],
            "missing_versions": [migration.version for migration in MIGRATIONS],
            "mismatches": [],
            "passed": False,
        },
        "passed": False,
    }
    if not path.exists():
        base["error_code"] = "database_missing"
        return base
    if path.is_symlink() or not path.is_file():
        base["error_code"] = "unsafe_database_path"
        return base

    try:
        with sqlite3.connect(_readonly_uri(path), uri=True, timeout=10.0) as connection:
            connection.execute("PRAGMA query_only=ON")
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            integrity_messages = [str(row[0]) for row in integrity_rows]
            integrity_ok = integrity_messages == ["ok"]
            foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            migration_rows = _migration_rows(connection)
    except sqlite3.Error:
        base["error_code"] = "sqlite_read_failed"
        return base

    migration_history = _migration_status(migration_rows, MIGRATIONS)
    base.update(
        {
            "integrity_check": "ok" if integrity_ok else "failed",
            "integrity_message_count": len(integrity_messages),
            "foreign_key_violation_count": len(foreign_key_rows),
            "migration_history": migration_history,
            "passed": integrity_ok
            and not foreign_key_rows
            and bool(migration_rows)
            and bool(migration_history["passed"]),
        }
    )
    if not base["passed"]:
        base["error_code"] = "database_validation_failed"
    return base


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only SQLite integrity and migration-history validation."
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = check_database(args.database)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.backup_restore_smoke import run_backup_restore_smoke
from tools.database_integrity_check import MIGRATIONS, check_database


def _create_migration_history(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, "
            "applied_at_ms INTEGER NOT NULL)"
        )
        connection.execute("PRAGMA foreign_keys=ON")
        for migration in MIGRATIONS:
            connection.executescript(migration.sql)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, checksum, applied_at_ms) "
                "VALUES (?, ?, ?, 1)",
                (migration.version, migration.name, migration.checksum),
            )
        connection.commit()


def test_database_integrity_check_validates_migration_history(tmp_path: Path) -> None:
    database = tmp_path / "douyin_recorder.db"
    _create_migration_history(database)

    report = check_database(database)

    assert report["passed"] is True
    assert report["integrity_check"] == "ok"
    assert report["foreign_key_violation_count"] == 0
    assert report["migration_history"]["schema_version"] == MIGRATIONS[-1].version
    assert report["migration_history"]["passed"] is True
    assert str(tmp_path.resolve()) not in json.dumps(report)


def test_database_integrity_check_rejects_checksum_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "douyin_recorder.db"
    _create_migration_history(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = 'changed' WHERE version = ?",
            (MIGRATIONS[-1].version,),
        )
        connection.commit()

    report = check_database(database)

    assert report["passed"] is False
    assert report["error_code"] == "database_validation_failed"
    assert report["migration_history"]["mismatches"] == [
        {
            "version": MIGRATIONS[-1].version,
            "name_matches": True,
            "checksum_matches": False,
        }
    ]


def test_backup_restore_smoke_restores_schema_and_marker(tmp_path: Path) -> None:
    report = run_backup_restore_smoke(tmp_path / "backups")

    assert report["passed"] is True
    assert report["archive"]["checksum_matches"] is True
    assert report["database"]["source_schema_version"] == MIGRATIONS[-1].version
    assert report["database"]["restored_schema_version"] == MIGRATIONS[-1].version
    assert report["database"]["marker_row_count"] == 1
    assert report["records_manifest_file_count"] == 1
    rendered = json.dumps(report, sort_keys=True)
    assert str(tmp_path.resolve()) not in rendered
    assert all(term not in rendered.lower() for term in _PRIVATE_TERMS)


_PRIVATE_TERMS = {
    "sessionid",
    "authorization",
    "raw_payload_json",
    "frame_base64",
}

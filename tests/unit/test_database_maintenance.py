from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.db.core import Database
from app.paths import RuntimePaths
from app.settings import Settings
from tools.backup_runtime import create_runtime_backup
from tools.database_maintenance import (
    DatabaseMaintenanceError,
    inspect_database_maintenance,
    run_database_maintenance,
)
from tools.database_maintenance_smoke import run_database_maintenance_smoke


def _settings(tmp_path: Path) -> Settings:
    runtime_root = tmp_path / "runtime"
    paths = RuntimePaths(
        root=runtime_root,
        config_dir=runtime_root / "config",
        userdata_dir=runtime_root / "userdata",
        records_dir=runtime_root / "records",
        database_path=runtime_root / "userdata" / "douyin_recorder.db",
    )
    return Settings.load(root=Path.cwd(), paths=paths, environ={})


def _initialize(path: Path) -> None:
    async def scenario() -> None:
        database = Database(path)
        await database.initialize()
        await database.execute(
            "INSERT INTO rooms(room_key, room_url, enabled, created_at_ms, updated_at_ms) "
            "VALUES ('maintenance-room', 'https://live.douyin.com/73504089679', 1, 1, 1)"
        )
        await database.close()

    asyncio.run(scenario())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_database_maintenance_plan_is_read_only(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _initialize(settings.paths.database_path)
    before = _sha256(settings.paths.database_path)

    report = inspect_database_maintenance(settings.paths.database_path)

    assert report["passed"] is True
    assert report["mode"] == "plan"
    assert report["actions"] == ["wal_checkpoint_truncate", "pragma_optimize"]
    assert report["vacuum"]["execution_supported"] is False
    assert _sha256(settings.paths.database_path) == before
    rendered = json.dumps(report, sort_keys=True)
    assert str(tmp_path.resolve()) not in rendered


def test_database_maintenance_apply_backs_up_and_preserves_schema(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _initialize(settings.paths.database_path)
    backup_dir = tmp_path / "backups"

    report = run_database_maintenance(
        settings.paths.database_path,
        apply=True,
        confirm_stopped=True,
        backup_dir=backup_dir,
        backup_creator=lambda output: create_runtime_backup(output, settings=settings),
    )

    assert report["passed"] is True
    assert report["backup"]["verified"] is True
    assert report["checkpoint"]["busy"] == 0
    assert report["optimize_executed"] is True
    assert report["migration_state_unchanged"] is True
    assert report["before"]["validation"]["migration_history"]["schema_version"] == 6
    assert report["after"]["validation"]["migration_history"]["schema_version"] == 6
    assert list(backup_dir.glob("*.zip"))
    with sqlite3.connect(settings.paths.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM rooms WHERE room_key = 'maintenance-room'"
        ).fetchone()[0] == 1


def test_database_maintenance_requires_explicit_stopped_confirmation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _initialize(settings.paths.database_path)

    with pytest.raises(DatabaseMaintenanceError, match="confirm-stopped"):
        run_database_maintenance(
            settings.paths.database_path,
            apply=True,
            backup_dir=tmp_path / "backups",
            backup_creator=lambda output: create_runtime_backup(output, settings=settings),
        )


def test_database_maintenance_fails_closed_when_database_is_busy(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _initialize(settings.paths.database_path)
    locker = sqlite3.connect(settings.paths.database_path)
    locker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(DatabaseMaintenanceError, match="busy"):
            run_database_maintenance(
                settings.paths.database_path,
                apply=True,
                confirm_stopped=True,
                backup_dir=tmp_path / "backups",
                backup_creator=lambda output: create_runtime_backup(output, settings=settings),
                busy_timeout_ms=100,
            )
    finally:
        locker.rollback()
        locker.close()


def test_database_maintenance_rejects_migration_checksum_change(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _initialize(settings.paths.database_path)
    with sqlite3.connect(settings.paths.database_path) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = 'changed' WHERE version = 6"
        )
        connection.commit()

    report = inspect_database_maintenance(settings.paths.database_path)

    assert report["passed"] is False
    assert report["error_code"] == "database_precheck_failed"
    assert report["validation"]["migration_history"]["mismatches"] == [
        {"version": 6, "name_matches": True, "checksum_matches": False}
    ]


def test_database_maintenance_smoke_runs_production_path(tmp_path: Path) -> None:
    report = run_database_maintenance_smoke(tmp_path / "maintenance-smoke")

    assert report["passed"] is True
    assert report["schema_version"] == 6
    assert report["backup_verified"] is True
    assert report["checkpoint_busy"] == 0
    assert report["marker_row_count"] == 1
    assert report["live_verified"] is False

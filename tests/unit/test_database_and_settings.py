from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from app.db import Database
from app.paths import RuntimePaths
from app.settings import Settings, SettingsError, sync_config


def _write_template(root: Path, *, host: str = "127.0.0.1") -> None:
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "config.json.default").write_text(
        json.dumps(
            {
                "server": {"host": host, "port": 3399, "auth_required": False},
                "recording": {"protocol": "flv", "quality": "origin"},
            }
        ),
        encoding="utf-8",
    )


def test_settings_create_actual_config_and_runtime_directories(tmp_path: Path) -> None:
    _write_template(tmp_path)
    settings = Settings.load(
        root=tmp_path,
        paths=RuntimePaths.defaults(tmp_path),
        environ={},
    )
    assert settings.host == "127.0.0.1"
    assert settings.port == 3399
    assert settings.config_path.is_file()
    assert settings.paths.userdata_dir.is_dir()
    assert settings.paths.records_dir.is_dir()
    assert settings.paths.database_path.parent == settings.paths.userdata_dir


def test_settings_refuse_public_bind_before_auth_exists(tmp_path: Path) -> None:
    _write_template(tmp_path, host="0.0.0.0")
    with pytest.raises(SettingsError, match="只允许绑定"):
        Settings.load(root=tmp_path, paths=RuntimePaths.defaults(tmp_path), environ={})


def test_config_sync_adds_defaults_without_overwriting_user_values(tmp_path: Path) -> None:
    template = tmp_path / "config.json.default"
    actual = tmp_path / "config.json"
    template.write_text(
        json.dumps({"server": {"port": 3399}, "feature": {"enabled": True}}),
        encoding="utf-8",
    )
    actual.write_text(
        json.dumps({"server": {"port": 4000}, "custom": 0}),
        encoding="utf-8",
    )
    merged = sync_config(template, actual)
    assert merged["server"]["port"] == 4000
    assert merged["feature"]["enabled"] is True
    assert merged["custom"] == 0
    assert actual.with_suffix(".json.bak").is_file()


def test_database_migrations_constraints_and_consistent_backup(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "userdata" / "douyin_recorder.db")
        await database.initialize()
        assert await database.schema_version() == 1
        assert str(await database.pragma("journal_mode")).casefold() == "wal"
        assert int(await database.pragma("foreign_keys")) == 1

        await database.execute(
            "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, ?, ?)",
            ("runtime-a", "test", 1),
        )
        await database.execute(
            "INSERT INTO rooms(room_key, room_url, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?)",
            ("room-a", "https://live.douyin.com/73504089679", 1, 1),
        )
        await database.execute(
            "INSERT INTO sessions(id, room_key, status, started_at_ms, runtime_instance_id) "
            "VALUES (?, ?, 'active', ?, ?)",
            ("session-a", "room-a", 1, "runtime-a"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            await database.execute(
                "INSERT INTO sessions(id, room_key, status, started_at_ms, runtime_instance_id) "
                "VALUES (?, ?, 'active', ?, ?)",
                ("session-b", "room-a", 2, "runtime-a"),
            )

        backup = tmp_path / "backup" / "douyin_recorder.db"
        await database.backup_to(backup)
        await database.close()
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("SELECT COUNT(*) FROM rooms").fetchone() == (1,)

    asyncio.run(scenario())

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.diagnostics_report import build_report


def test_diagnostics_report_does_not_expose_private_values(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    private_value = "private-value-must-not-appear"
    (config / "config.json").write_text(
        json.dumps({"private_setting": private_value}), encoding="utf-8"
    )
    (config / "runtime.env").write_text(
        f"PRIVATE_SETTING={private_value}\n", encoding="utf-8"
    )

    report = build_report(tmp_path)
    rendered = json.dumps(report, sort_keys=True)

    assert private_value not in rendered
    assert str(tmp_path.resolve()) not in rendered
    assert report["configuration"]["config_json"]["present"] is True
    assert report["configuration"]["runtime_env"]["present"] is True
    assert report["database"]["exists"] is False
    assert report["redaction_policy"]["sensitive_values"] == "excluded"


def test_diagnostics_report_uses_runtime_database_filename(tmp_path: Path) -> None:
    database = tmp_path / "userdata" / "douyin_recorder.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, "
            "applied_at_ms INTEGER NOT NULL)"
        )
        connection.commit()

    report = build_report(tmp_path)

    assert report["database"]["database_filename"] == "douyin_recorder.db"
    assert "app.sqlite3" not in json.dumps(report)

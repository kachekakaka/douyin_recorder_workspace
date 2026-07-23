from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.core import Database  # noqa: E402
from app.paths import RuntimePaths  # noqa: E402
from app.settings import Settings  # noqa: E402
from tools.backup_runtime import create_runtime_backup  # noqa: E402
from tools.database_maintenance import (  # noqa: E402
    inspect_database_maintenance,
    run_database_maintenance,
)

_MARKER_ROOM_KEY = "maintenance-smoke-room"
_MARKER_ROOM_URL = "https://live.douyin.com/73504089679"


def _settings(runtime_root: Path) -> Settings:
    paths = RuntimePaths(
        root=runtime_root,
        config_dir=runtime_root / "config",
        userdata_dir=runtime_root / "userdata",
        records_dir=runtime_root / "records",
        database_path=runtime_root / "userdata" / "douyin_recorder.db",
    )
    return Settings.load(root=ROOT, paths=paths, environ={})


async def _initialize(path: Path) -> int:
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
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM rooms WHERE room_key = ? AND room_url = ?",
            (_MARKER_ROOM_KEY, _MARKER_ROOM_URL),
        ).fetchone()
    return int(row[0]) if row else 0


def run_database_maintenance_smoke(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists() and output_dir.is_symlink():
        raise RuntimeError("output directory must not be a symbolic link")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="douyin-database-maintenance-") as temp:
        settings = _settings(Path(temp) / "runtime")
        schema_version = asyncio.run(_initialize(settings.paths.database_path))
        plan = inspect_database_maintenance(settings.paths.database_path)
        applied = run_database_maintenance(
            settings.paths.database_path,
            apply=True,
            confirm_stopped=True,
            backup_dir=output_dir,
            backup_creator=lambda destination: create_runtime_backup(
                destination, settings=settings
            ),
        )
        marker_count = _marker_count(settings.paths.database_path)
    passed = bool(
        plan.get("passed")
        and applied.get("passed")
        and applied.get("checkpoint", {}).get("busy") == 0
        and applied.get("migration_state_unchanged") is True
        and marker_count == 1
        and schema_version == 6
    )
    return {
        "smoke_version": 1,
        "schema_version": schema_version,
        "plan_passed": bool(plan.get("passed")),
        "apply_passed": bool(applied.get("passed")),
        "backup_verified": bool(applied.get("backup", {}).get("verified")),
        "checkpoint_busy": applied.get("checkpoint", {}).get("busy"),
        "optimize_executed": applied.get("optimize_executed"),
        "migration_state_unchanged": applied.get("migration_state_unchanged"),
        "marker_row_count": marker_count,
        "vacuum_execution_supported": False,
        "uses_network": False,
        "live_verified": False,
        "passed": passed,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Isolated safe SQLite maintenance and backup smoke."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--json-output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run_database_maintenance_smoke(args.output_dir)
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"[失败] 数据库维护演练失败: {type(exc).__name__}", file=sys.stderr)
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

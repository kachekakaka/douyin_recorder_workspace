from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import __version__  # noqa: E402
from tools.database_integrity_check import check_database  # noqa: E402


def _read_contract(root: Path) -> dict[str, object]:
    path = root / "app" / "douyin" / "contracts" / "provisional_v1.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"present": False, "readable": False}
    if not isinstance(payload, dict):
        return {"present": True, "readable": False}
    return {
        "present": True,
        "readable": True,
        "target_method": payload.get("target_method"),
        "live_verified": payload.get("live_verified"),
    }


def _file_summary(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"present": False}
    if path.is_symlink() or not path.is_file():
        return {"present": True, "safe_type": False}
    return {"present": True, "safe_type": True, "bytes": path.stat().st_size}


def build_report(root: Path, *, database_path: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    database = (database_path or root / "userdata" / "douyin_recorder.db").resolve()
    database_report = check_database(database)
    return {
        "diagnostic_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "application_version": __version__,
        "runtime": {
            "python_version": platform.python_version(),
            "system": platform.system(),
            "system_release": platform.release(),
            "machine": platform.machine(),
        },
        "repository": {"name": root.name},
        "configuration": {
            "config_json": _file_summary(root / "config" / "config.json"),
            "runtime_env": _file_summary(root / "config" / "runtime.env"),
        },
        "database": database_report,
        "protocol_contract": _read_contract(root),
        "redaction_policy": {
            "sensitive_values": "excluded",
            "full_urls": "excluded",
            "raw_protocol_data": "excluded",
            "absolute_runtime_paths": "excluded",
        },
    }


def _write_json(path: Path, rendered: str) -> None:
    if path.exists() and path.is_symlink():
        raise OSError("refusing to replace a symbolic-link output")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a redacted diagnostics report.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = build_report(args.root, database_path=args.database)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output is None:
            print(rendered, end="")
        else:
            _write_json(args.output, rendered)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"[失败] 诊断报告生成失败: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

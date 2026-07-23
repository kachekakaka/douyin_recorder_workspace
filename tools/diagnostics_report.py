from __future__ import annotations

import argparse
import json
import platform
import sqlite3
from pathlib import Path
from typing import Any


def _schema_version(db_path: Path) -> int | None:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("PRAGMA user_version").fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        return None


def build_report(root: Path) -> dict[str, Any]:
    return {
        "diagnostic_version": 1,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "repository": root.name,
        "database": {
            "schema_version": _schema_version(root / "userdata" / "app.sqlite3"),
        },
        "safety": {
            "contains_cookie": False,
            "contains_full_stream_url": False,
            "contains_raw_payload": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate safe diagnostics JSON")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = build_report(args.root.resolve())
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def check_database(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"path": str(path), "exists": False, "integrity": "missing"}

    with sqlite3.connect(path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        version = conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()

    return {
        "path": str(path),
        "exists": True,
        "integrity": integrity,
        "schema_version": version[0] if version else None,
        "passed": integrity == "ok",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check SQLite integrity safely.")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(json.dumps(check_database(args.database), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json

from app.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare runtime directories and actual config")
    parser.add_argument("--json", action="store_true", help="print machine-readable startup data")
    args = parser.parse_args()
    settings = Settings.load()
    payload = {
        "config_path": str(settings.config_path),
        "database_path": str(settings.paths.database_path),
        "records_dir": str(settings.paths.records_dir),
        "url": settings.public_url,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"配置：{settings.config_path}")
        print(f"数据库：{settings.paths.database_path}")
        print(f"录像目录：{settings.paths.records_dir}")
        print(f"启动地址：{settings.public_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

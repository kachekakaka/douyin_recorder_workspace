from __future__ import annotations

from app.settings import Settings


def main() -> int:
    settings = Settings.load()
    print(f"OPEN_URL={settings.public_url}")
    print(f"BIND_HOST={settings.host}")
    print(f"BIND_PORT={settings.port}")
    print(f"DATABASE_PATH={settings.paths.database_path}")
    print(f"RECORDS_PATH={settings.paths.records_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

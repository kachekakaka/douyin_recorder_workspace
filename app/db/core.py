from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import TypeVar

import aiosqlite

from app.db.migrations import MIGRATIONS, Migration

T = TypeVar("T")
WriteOperation = Callable[[aiosqlite.Connection], Awaitable[T]]


class MigrationError(RuntimeError):
    """Raised when recorded migration history no longer matches source."""


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Database:
    """SQLite owner with one ordered writer connection."""

    def __init__(self, path: Path):
        self.path = path
        self._writer: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._writer is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA synchronous=NORMAL")
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute("PRAGMA busy_timeout=5000")
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at_ms INTEGER NOT NULL
            )
            """
        )
        await connection.commit()
        self._writer = connection
        try:
            await self._apply_migrations()
        except Exception:
            await self.close()
            raise

    async def _applied_migrations(self) -> dict[int, tuple[str, str]]:
        connection = self._require_writer()
        cursor = await connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {int(row[0]): (str(row[1]), str(row[2])) for row in rows}

    async def _apply_migrations(self) -> None:
        connection = self._require_writer()
        applied = await self._applied_migrations()
        known_versions = {migration.version for migration in MIGRATIONS}
        unknown = sorted(set(applied) - known_versions)
        if unknown:
            raise MigrationError(f"数据库包含当前代码未知的迁移版本: {unknown}")

        for migration in MIGRATIONS:
            recorded = applied.get(migration.version)
            if recorded is not None:
                name, checksum = recorded
                if name != migration.name or checksum != migration.checksum:
                    raise MigrationError(
                        f"迁移 {migration.version} 与数据库记录不一致，拒绝静默继续"
                    )
                continue
            await self._validate_migration_preconditions(connection, migration)
            await self._apply_one_migration(connection, migration)

    async def _validate_migration_preconditions(
        self,
        connection: aiosqlite.Connection,
        migration: Migration,
    ) -> None:
        if migration.version != 3:
            return
        cursor = await connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT room_url
                FROM rooms
                GROUP BY room_url
                HAVING COUNT(*) > 1
            )
            """
        )
        row = await cursor.fetchone()
        await cursor.close()
        duplicate_groups = int(row[0]) if row else 0
        if duplicate_groups:
            raise MigrationError(
                "迁移 3 无法创建规范化 room_url 唯一索引："
                f"检测到 {duplicate_groups} 组重复数据，请先人工去重"
            )

    async def _apply_one_migration(
        self, connection: aiosqlite.Connection, migration: Migration
    ) -> None:
        applied_at_ms = int(time.time() * 1000)
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{migration.sql.rstrip()}\n"
            "INSERT INTO schema_migrations(version, name, checksum, applied_at_ms) VALUES ("
            f"{migration.version}, {_sql_literal(migration.name)}, "
            f"{_sql_literal(migration.checksum)}, {applied_at_ms});\n"
            "COMMIT;\n"
        )
        try:
            await connection.executescript(script)
        except Exception:
            with suppress(Exception):
                await connection.rollback()
            raise

    def _require_writer(self) -> aiosqlite.Connection:
        if self._writer is None:
            raise RuntimeError("数据库尚未初始化")
        return self._writer

    async def write(self, operation: WriteOperation[T]) -> T:
        async with self._write_lock:
            connection = self._require_writer()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                result = await operation(connection)
                await connection.commit()
                return result
            except Exception:
                await connection.rollback()
                raise

    async def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> None:
        async def operation(connection: aiosqlite.Connection) -> None:
            await connection.execute(sql, parameters)

        await self.write(operation)

    async def fetch_one(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> dict[str, object] | None:
        async with aiosqlite.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.execute("PRAGMA busy_timeout=5000")
            cursor = await connection.execute(sql, parameters)
            row = await cursor.fetchone()
            await cursor.close()
            return dict(row) if row is not None else None

    async def fetch_all(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> list[dict[str, object]]:
        async with aiosqlite.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.execute("PRAGMA busy_timeout=5000")
            cursor = await connection.execute(sql, parameters)
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]

    async def schema_version(self) -> int:
        row = await self.fetch_one(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        )
        return int(row["version"]) if row else 0

    async def pragma(self, name: str) -> object:
        if name not in {"journal_mode", "synchronous", "foreign_keys", "busy_timeout"}:
            raise ValueError("不允许的 PRAGMA")
        connection = self._require_writer()
        cursor = await connection.execute(f"PRAGMA {name}")
        row = await cursor.fetchone()
        await cursor.close()
        return row[0] if row else None

    async def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with self._write_lock:
            source = self._require_writer()
            target = await aiosqlite.connect(destination)
            try:
                await source.backup(target)
                await target.commit()
            finally:
                await target.close()

    async def close(self) -> None:
        if self._writer is not None:
            await self._writer.close()
            self._writer = None

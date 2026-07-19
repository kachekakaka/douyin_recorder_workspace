from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import aiosqlite

from app.db import Database
from app.rooms.models import RoomCreate, RoomPatch, RoomRecord


class RoomNotFoundError(LookupError):
    """Raised when a room key does not exist."""


class RoomAlreadyExistsError(ValueError):
    """Raised when room_key already exists."""


_ROOM_COLUMNS = (
    "room_key",
    "room_url",
    "enabled",
    "quality",
    "protocol",
    "poll_interval_seconds",
    "created_at_ms",
    "updated_at_ms",
)


class RoomRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def list_rooms(self) -> list[RoomRecord]:
        rows = await self.database.fetch_all(
            f"SELECT {', '.join(_ROOM_COLUMNS)} FROM rooms ORDER BY room_key"
        )
        checks = await self.database.fetch_all(
            """
            SELECT rc.*
            FROM room_checks AS rc
            JOIN (
                SELECT room_key, MAX(id) AS max_id
                FROM room_checks
                GROUP BY room_key
            ) AS latest ON latest.max_id = rc.id
            """
        )
        check_map = {str(row["room_key"]): self._decode_check(row) for row in checks}
        return [self._decode_room(row, check_map.get(str(row["room_key"]))) for row in rows]

    async def get_room(self, room_key: str) -> RoomRecord:
        row = await self.database.fetch_one(
            f"SELECT {', '.join(_ROOM_COLUMNS)} FROM rooms WHERE room_key = ?",
            (room_key,),
        )
        if row is None:
            raise RoomNotFoundError(room_key)
        latest = await self.database.fetch_one(
            "SELECT * FROM room_checks WHERE room_key = ? ORDER BY id DESC LIMIT 1",
            (room_key,),
        )
        return self._decode_room(row, self._decode_check(latest) if latest else None)

    async def create_room(self, payload: RoomCreate) -> RoomRecord:
        now_ms = int(time.time() * 1000)

        async def operation(connection: aiosqlite.Connection) -> None:
            await connection.execute(
                """
                INSERT INTO rooms(
                    room_key, room_url, enabled, quality, protocol,
                    poll_interval_seconds, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.room_key,
                    payload.room_url,
                    int(payload.enabled),
                    payload.quality,
                    payload.protocol,
                    payload.poll_interval_seconds,
                    now_ms,
                    now_ms,
                ),
            )

        try:
            await self.database.write(operation)
        except sqlite3.IntegrityError as exc:
            raise RoomAlreadyExistsError(payload.room_key) from exc
        return await self.get_room(payload.room_key)

    async def update_room(self, room_key: str, patch: RoomPatch) -> RoomRecord:
        changes = patch.model_dump(exclude_unset=True)
        if not changes:
            raise ValueError("没有可修改字段")
        await self.get_room(room_key)
        allowed = {
            "room_url",
            "enabled",
            "quality",
            "protocol",
            "poll_interval_seconds",
        }
        if not set(changes).issubset(allowed):
            raise ValueError("包含不允许修改的字段")
        changes["updated_at_ms"] = int(time.time() * 1000)
        columns = list(changes)
        values: list[object] = [
            int(value) if key == "enabled" else value for key, value in changes.items()
        ]
        assignments = ", ".join(f"{column} = ?" for column in columns)
        await self.database.execute(
            f"UPDATE rooms SET {assignments} WHERE room_key = ?",
            (*values, room_key),
        )
        return await self.get_room(room_key)

    async def set_enabled(self, room_key: str, enabled: bool) -> RoomRecord:
        return await self.update_room(room_key, RoomPatch(enabled=enabled))

    async def record_check(self, room_key: str, snapshot: dict[str, object]) -> dict[str, object]:
        checked_at_ms = int(snapshot.get("checked_at_ms") or int(time.time() * 1000))
        detail_json = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        await self.database.execute(
            """
            INSERT INTO room_checks(
                room_key, checked_at_ms, live_state, http_status, final_host, final_path,
                external_room_id, web_rid, title, stream_candidate_count, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                room_key,
                checked_at_ms,
                str(snapshot.get("live_state") or "unknown"),
                snapshot.get("http_status"),
                str(snapshot.get("final_host") or ""),
                str(snapshot.get("final_path") or "/"),
                snapshot.get("external_room_id"),
                snapshot.get("web_rid"),
                str(snapshot.get("title") or "")[:500],
                int(snapshot.get("stream_candidate_count") or 0),
                detail_json,
            ),
        )
        return snapshot

    @staticmethod
    def _decode_room(row: dict[str, object], latest_check: dict[str, object] | None) -> RoomRecord:
        return RoomRecord(
            room_key=str(row["room_key"]),
            room_url=str(row["room_url"]),
            enabled=bool(row["enabled"]),
            quality=str(row["quality"]),
            protocol=str(row["protocol"]),
            poll_interval_seconds=int(row["poll_interval_seconds"]),
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
            latest_check=latest_check,
        )

    @staticmethod
    def _decode_check(row: dict[str, Any]) -> dict[str, object]:
        try:
            value = json.loads(str(row.get("detail_json") or "{}"))
        except json.JSONDecodeError:
            value = {}
        return value if isinstance(value, dict) else {}

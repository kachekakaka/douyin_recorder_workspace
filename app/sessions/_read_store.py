from __future__ import annotations

from app.db import Database
from app.sessions._rows import (
    EVENT_PUBLIC_COLUMNS,
    INTERVAL_COLUMNS,
    decode_event,
    decode_interval,
    validate_page,
)
from app.sessions.models import (
    RecipientEventRecord,
    RecipientIntervalRecord,
    RecipientSessionState,
)
from app.sessions.repository_errors import RecipientSessionNotFoundError


class RecipientSessionReadStore:
    database: Database

    async def get_session(self, session_id: str) -> dict[str, object]:
        row = await self.database.fetch_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if row is None:
            raise RecipientSessionNotFoundError(session_id)
        return row

    async def get_state(self, room_key: str) -> RecipientSessionState:
        session = await self.database.fetch_one(
            """
            SELECT id, room_key, status, protocol_contract_sha256, protocol_live_verified
            FROM sessions
            WHERE room_key = ?
            ORDER BY started_at_ms DESC, id DESC
            LIMIT 1
            """,
            (room_key,),
        )
        if session is None:
            return RecipientSessionState(
                room_key=room_key,
                session_id=None,
                session_status=None,
                interval=None,
                protocol_contract_sha256=None,
                protocol_live_verified=False,
            )
        interval = await self.database.fetch_one(
            f"SELECT {', '.join(INTERVAL_COLUMNS)} FROM recipient_intervals "
            "WHERE session_id = ? AND ended_at_ms IS NULL ORDER BY id DESC LIMIT 1",
            (session["id"],),
        )
        return RecipientSessionState(
            room_key=room_key,
            session_id=str(session["id"]),
            session_status=str(session["status"]),
            interval=decode_interval(interval) if interval else None,
            protocol_contract_sha256=str(session["protocol_contract_sha256"] or ""),
            protocol_live_verified=bool(session["protocol_live_verified"]),
        )

    async def get_event(self, event_id: int) -> RecipientEventRecord:
        row = await self.database.fetch_one(
            f"SELECT {', '.join(EVENT_PUBLIC_COLUMNS)} FROM recipient_events WHERE id = ?",
            (event_id,),
        )
        if row is None:
            raise RecipientSessionNotFoundError(f"event:{event_id}")
        return decode_event(row)

    async def list_events(
        self,
        *,
        room_key: str,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RecipientEventRecord]:
        validate_page(limit=limit, offset=offset)
        selected = await self._resolve_session_id(room_key, session_id)
        if selected is None:
            return []
        rows = await self.database.fetch_all(
            f"SELECT {', '.join(EVENT_PUBLIC_COLUMNS)} FROM recipient_events "
            "WHERE session_id = ? ORDER BY received_at_ms, id LIMIT ? OFFSET ?",
            (selected, limit, offset),
        )
        return [decode_event(row) for row in rows]

    async def list_intervals(
        self,
        *,
        room_key: str,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RecipientIntervalRecord]:
        validate_page(limit=limit, offset=offset)
        selected = await self._resolve_session_id(room_key, session_id)
        if selected is None:
            return []
        rows = await self.database.fetch_all(
            f"SELECT {', '.join(INTERVAL_COLUMNS)} FROM recipient_intervals "
            "WHERE session_id = ? ORDER BY started_at_ms, id LIMIT ? OFFSET ?",
            (selected, limit, offset),
        )
        return [decode_interval(row) for row in rows]

    async def _resolve_session_id(self, room_key: str, session_id: str | None) -> str | None:
        if session_id is None:
            row = await self.database.fetch_one(
                "SELECT id FROM sessions WHERE room_key = ? "
                "ORDER BY started_at_ms DESC, id DESC LIMIT 1",
                (room_key,),
            )
            return str(row["id"]) if row else None
        row = await self.database.fetch_one(
            "SELECT id FROM sessions WHERE id = ? AND room_key = ?",
            (session_id, room_key),
        )
        if row is None:
            raise RecipientSessionNotFoundError(session_id)
        return str(row["id"])

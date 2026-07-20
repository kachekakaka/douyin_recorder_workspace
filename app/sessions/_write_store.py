from __future__ import annotations

import json
import sqlite3

import aiosqlite

from app.db import Database
from app.douyin.recipient import DecodedRecipientEvent
from app.sessions._rows import validate_raw_payload_json
from app.sessions.models import RecipientProjectionResult, RecipientSessionState
from app.sessions.repository_errors import (
    RecipientSessionNotFoundError,
    RecipientSessionStateError,
)


class RecipientSessionWriteStore:
    database: Database

    async def start_session(
        self,
        *,
        session_id: str,
        room_key: str,
        started_at_ms: int,
        started_monotonic_ns: int,
        runtime_instance_id: str,
        protocol_contract_sha256: str,
        protocol_live_verified: bool,
        external_room_id: str | None = None,
        title: str = "",
    ) -> RecipientSessionState:
        if not session_id or not room_key or not runtime_instance_id:
            raise RecipientSessionStateError("session_id、room_key 与 runtime_instance_id 必填")
        if started_at_ms < 0 or started_monotonic_ns < 0:
            raise RecipientSessionStateError("场次起始时间无效")
        if not protocol_contract_sha256:
            raise RecipientSessionStateError("协议 contract SHA-256 必填")

        async def operation(connection: aiosqlite.Connection) -> None:
            await connection.execute(
                """
                INSERT INTO sessions(
                    id, room_key, external_room_id, title, status, started_at_ms,
                    runtime_instance_id, protocol_contract_sha256,
                    protocol_live_verified, started_monotonic_ns
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    room_key,
                    external_room_id,
                    title[:500],
                    started_at_ms,
                    runtime_instance_id,
                    protocol_contract_sha256,
                    int(protocol_live_verified),
                    started_monotonic_ns,
                ),
            )
            await self._open_interval(
                connection,
                session_id=session_id,
                status="waiting",
                reason="waiting_first_event",
                recipient_key=None,
                recipient_user_id=None,
                recipient_user_open_id=None,
                at_ms=started_at_ms,
                monotonic_ns=started_monotonic_ns,
                runtime_instance_id=runtime_instance_id,
                start_event_id=None,
            )

        try:
            await self.database.write(operation)
        except sqlite3.IntegrityError as exc:
            raise RecipientSessionStateError("场次无法开始：房间、runtime 或 session 冲突") from exc
        return await self.get_state(room_key)  # type: ignore[attr-defined]

    async def apply_event(
        self,
        *,
        session_id: str,
        event: DecodedRecipientEvent,
        raw_payload_json: str,
    ) -> RecipientProjectionResult:
        validate_raw_payload_json(raw_payload_json)

        async def operation(connection: aiosqlite.Connection) -> tuple[str, int, str]:
            session = await self._require_active_session(connection, session_id)
            current = await self._require_open_interval(connection, session_id)
            existing = await self._existing_event(connection, session_id, event.dedup_key)
            if existing is not None:
                event_id = int(existing["id"])
                await connection.execute(
                    """
                    UPDATE recipient_events
                    SET duplicate_count = duplicate_count + 1,
                        last_received_at_ms = MAX(last_received_at_ms, ?)
                    WHERE id = ?
                    """,
                    (event.received_at_ms, event_id),
                )
                return "duplicate", event_id, str(session["room_key"])

            is_late = event.received_at_ms < int(current["started_at_ms"])
            event_id = await self._insert_event(
                connection,
                session_id=session_id,
                event=event,
                raw_payload_json=raw_payload_json,
                is_late=is_late,
            )
            room_key = str(session["room_key"])
            if is_late:
                return "late", event_id, room_key
            if event.recipient_key is None:
                if current["status"] == "unknown" and current["reason"] == "empty_recipient":
                    return "same-unknown", event_id, room_key
                await self._transition_from_event(
                    connection,
                    current=current,
                    event_id=event_id,
                    event=event,
                    status="unknown",
                    reason="empty_recipient",
                )
                return "unknown", event_id, room_key
            if current["status"] == "active" and current["recipient_key"] == event.recipient_key:
                return "same-recipient", event_id, room_key
            await self._transition_from_event(
                connection,
                current=current,
                event_id=event_id,
                event=event,
                status="active",
                reason="recipient_event_received",
            )
            return "active", event_id, room_key

        outcome, event_id, room_key = await self.database.write(operation)
        event_record = await self.get_event(event_id)  # type: ignore[attr-defined]
        state = await self.get_state(room_key)  # type: ignore[attr-defined]
        return RecipientProjectionResult(outcome=outcome, event=event_record, state=state)

    async def im_disconnected(
        self,
        *,
        session_id: str,
        at_ms: int,
        monotonic_ns: int,
        runtime_instance_id: str,
    ) -> RecipientSessionState:
        async def operation(connection: aiosqlite.Connection) -> str:
            session = await self._require_active_session(connection, session_id)
            current = await self._require_open_interval(connection, session_id)
            if current["status"] == "unknown" and current["reason"] == "im_disconnected":
                return str(session["room_key"])
            self._validate_close_clock(current, at_ms, monotonic_ns, runtime_instance_id)
            await self._close_interval(
                connection,
                current=current,
                at_ms=at_ms,
                monotonic_ns=monotonic_ns,
                runtime_instance_id=runtime_instance_id,
                end_event_id=None,
            )
            await self._open_interval(
                connection,
                session_id=session_id,
                status="unknown",
                reason="im_disconnected",
                recipient_key=None,
                recipient_user_id=None,
                recipient_user_open_id=None,
                at_ms=at_ms,
                monotonic_ns=monotonic_ns,
                runtime_instance_id=runtime_instance_id,
                start_event_id=None,
            )
            return str(session["room_key"])

        room_key = await self.database.write(operation)
        return await self.get_state(room_key)  # type: ignore[attr-defined]

    async def im_reconnected(self, *, session_id: str) -> RecipientSessionState:
        session = await self.get_session(session_id)  # type: ignore[attr-defined]
        if session["status"] != "active":
            raise RecipientSessionStateError("场次不是 active")
        return await self.get_state(str(session["room_key"]))  # type: ignore[attr-defined]

    async def end_session(
        self,
        *,
        session_id: str,
        at_ms: int,
        monotonic_ns: int,
        runtime_instance_id: str,
        end_reason: str,
        final_status: str = "ended",
    ) -> dict[str, object]:
        if final_status not in {"ended", "interrupted", "failed"}:
            raise RecipientSessionStateError("非法场次结束状态")

        async def operation(connection: aiosqlite.Connection) -> dict[str, object]:
            session = await self._require_active_session(connection, session_id)
            current = await self._require_open_interval(connection, session_id)
            self._validate_close_clock(current, at_ms, monotonic_ns, runtime_instance_id)
            await self._close_interval(
                connection,
                current=current,
                at_ms=at_ms,
                monotonic_ns=monotonic_ns,
                runtime_instance_id=runtime_instance_id,
                end_event_id=None,
            )
            await connection.execute(
                """
                UPDATE sessions
                SET status = ?, ended_at_ms = ?, end_reason = ?,
                    ended_monotonic_ns = ?, ended_runtime_instance_id = ?
                WHERE id = ?
                """,
                (
                    final_status,
                    at_ms,
                    end_reason[:200],
                    monotonic_ns
                    if runtime_instance_id == session["runtime_instance_id"]
                    else None,
                    runtime_instance_id,
                    session_id,
                ),
            )
            return dict(session)

        return await self.database.write(operation)

    @staticmethod
    async def _existing_event(
        connection: aiosqlite.Connection, session_id: str, dedup_key: str
    ) -> aiosqlite.Row | None:
        cursor = await connection.execute(
            "SELECT id FROM recipient_events WHERE session_id = ? AND dedup_key = ? LIMIT 1",
            (session_id, dedup_key),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    @staticmethod
    async def _insert_event(
        connection: aiosqlite.Connection,
        *,
        session_id: str,
        event: DecodedRecipientEvent,
        raw_payload_json: str,
        is_late: bool,
    ) -> int:
        cursor = await connection.execute(
            """
            INSERT INTO recipient_events(
                session_id, dedup_key, msg_id, envelope_msg_id,
                server_event_at_ms, server_time_unit,
                received_at_ms, received_monotonic_ns, runtime_instance_id,
                recipient_user_id, recipient_user_open_id, recipient_key,
                change_reason_enum, extra_json, raw_payload_json, payload_hash,
                payload_size, unknown_fields_json, duplicate_count,
                first_received_at_ms, last_received_at_ms, is_late
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                session_id,
                event.dedup_key,
                event.msg_id,
                event.envelope_msg_id,
                event.server_event_at_ms,
                event.server_time_unit,
                event.received_at_ms,
                event.received_monotonic_ns,
                event.runtime_instance_id,
                event.recipient_user_id,
                event.recipient_user_open_id,
                event.recipient_key,
                event.change_reason_enum,
                json.dumps(event.extra, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                raw_payload_json,
                event.payload_hash,
                event.payload_size,
                json.dumps(
                    list(event.unknown_fields),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                event.received_at_ms,
                event.received_at_ms,
                int(is_late),
            ),
        )
        event_id = int(cursor.lastrowid)
        await cursor.close()
        return event_id

    async def _transition_from_event(
        self,
        connection: aiosqlite.Connection,
        *,
        current: aiosqlite.Row,
        event_id: int,
        event: DecodedRecipientEvent,
        status: str,
        reason: str,
    ) -> None:
        self._validate_close_clock(
            current,
            event.received_at_ms,
            event.received_monotonic_ns,
            event.runtime_instance_id,
        )
        await self._close_interval(
            connection,
            current=current,
            at_ms=event.received_at_ms,
            monotonic_ns=event.received_monotonic_ns,
            runtime_instance_id=event.runtime_instance_id,
            end_event_id=event_id,
        )
        await self._open_interval(
            connection,
            session_id=str(current["session_id"]),
            status=status,
            reason=reason,
            recipient_key=event.recipient_key if status == "active" else None,
            recipient_user_id=event.recipient_user_id if status == "active" else None,
            recipient_user_open_id=event.recipient_user_open_id if status == "active" else None,
            at_ms=event.received_at_ms,
            monotonic_ns=event.received_monotonic_ns,
            runtime_instance_id=event.runtime_instance_id,
            start_event_id=event_id,
        )

    @staticmethod
    async def _require_active_session(
        connection: aiosqlite.Connection, session_id: str
    ) -> aiosqlite.Row:
        cursor = await connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise RecipientSessionNotFoundError(session_id)
        if row["status"] != "active":
            raise RecipientSessionStateError("场次不是 active")
        return row

    @staticmethod
    async def _require_open_interval(
        connection: aiosqlite.Connection, session_id: str
    ) -> aiosqlite.Row:
        cursor = await connection.execute(
            """
            SELECT * FROM recipient_intervals
            WHERE session_id = ? AND ended_at_ms IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise RecipientSessionStateError("场次没有开放 recipient 区间")
        return row

    @staticmethod
    async def _close_interval(
        connection: aiosqlite.Connection,
        *,
        current: aiosqlite.Row,
        at_ms: int,
        monotonic_ns: int,
        runtime_instance_id: str,
        end_event_id: int | None,
    ) -> None:
        same_runtime = runtime_instance_id == current["runtime_instance_id"]
        await connection.execute(
            """
            UPDATE recipient_intervals
            SET ended_at_ms = ?, ended_monotonic_ns = ?,
                ended_runtime_instance_id = ?, end_event_id = ?
            WHERE id = ? AND ended_at_ms IS NULL
            """,
            (
                at_ms,
                monotonic_ns if same_runtime else None,
                runtime_instance_id,
                end_event_id,
                current["id"],
            ),
        )

    @staticmethod
    async def _open_interval(
        connection: aiosqlite.Connection,
        *,
        session_id: str,
        status: str,
        reason: str,
        recipient_key: str | None,
        recipient_user_id: str | None,
        recipient_user_open_id: str | None,
        at_ms: int,
        monotonic_ns: int,
        runtime_instance_id: str,
        start_event_id: int | None,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO recipient_intervals(
                session_id, status, reason, recipient_key,
                recipient_user_id, recipient_user_open_id,
                started_at_ms, ended_at_ms, started_monotonic_ns,
                ended_monotonic_ns, runtime_instance_id,
                ended_runtime_instance_id, start_event_id, end_event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, NULL, ?, NULL)
            """,
            (
                session_id,
                status,
                reason,
                recipient_key,
                recipient_user_id,
                recipient_user_open_id,
                at_ms,
                monotonic_ns,
                runtime_instance_id,
                start_event_id,
            ),
        )

    @staticmethod
    def _validate_close_clock(
        current: aiosqlite.Row,
        at_ms: int,
        monotonic_ns: int,
        runtime_instance_id: str,
    ) -> None:
        if at_ms < int(current["started_at_ms"]):
            raise RecipientSessionStateError("区间结束时间不能早于开始时间")
        if (
            runtime_instance_id == current["runtime_instance_id"]
            and monotonic_ns < int(current["started_monotonic_ns"])
        ):
            raise RecipientSessionStateError("同一 runtime_instance 内单调时钟不能倒退")

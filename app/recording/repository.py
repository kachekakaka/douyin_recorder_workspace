from __future__ import annotations

import json
import re
from typing import Any

import aiosqlite

from app.db import Database
from app.media import ProgressSnapshot, RecorderResult, RecordingPlan, SegmentEntry
from app.recording.models import (
    RecordingSegmentRecord,
    RecordingSessionRecord,
    RecordingState,
    progress_public_json,
)

_SEGMENT_NAME_RE = re.compile(r"^(?P<sequence>[0-9]{5})\.(?P<container>mkv|ts)$")
_SESSION_COLUMNS = (
    "id",
    "room_key",
    "external_room_id",
    "title",
    "status",
    "started_at_ms",
    "ended_at_ms",
    "end_reason",
    "runtime_instance_id",
    "recording_protocol",
    "recording_quality",
    "input_host",
    "input_path_sha256",
    "input_url_sha256",
    "input_query_keys_json",
    "recording_container",
    "segment_seconds",
    "ffmpeg_returncode",
    "stop_stage",
    "last_progress_json",
    "recording_error_code",
)
_SEGMENT_COLUMNS = (
    "id",
    "session_id",
    "relative_path",
    "sequence",
    "continuity_group",
    "requested_start_at_ms",
    "actual_start_at_ms",
    "actual_end_at_ms",
    "size_bytes",
    "status",
    "segment_start_seconds",
    "segment_end_seconds",
    "container",
    "media_suffix",
)


class RecordingSessionError(RuntimeError):
    """Base recording session error."""


class RecordingAlreadyActiveError(RecordingSessionError):
    """Raised when a room already has an active recording."""


class RecordingNotActiveError(RecordingSessionError):
    """Raised when an active recording is required."""


class RecordingSessionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get_state(self, room_key: str) -> RecordingState:
        row = await self.database.fetch_one(
            f"SELECT {', '.join(_SESSION_COLUMNS)} FROM sessions "
            "WHERE room_key = ? AND recording_protocol IS NOT NULL "
            "ORDER BY started_at_ms DESC, id DESC LIMIT 1",
            (room_key,),
        )
        session = self._decode_session(row) if row else None
        return RecordingState(
            room_key=room_key,
            active=bool(session and session.status == "active"),
            session=session,
        )

    async def get_session(self, session_id: str) -> RecordingSessionRecord:
        row = await self.database.fetch_one(
            f"SELECT {', '.join(_SESSION_COLUMNS)} FROM sessions "
            "WHERE id = ? AND recording_protocol IS NOT NULL",
            (session_id,),
        )
        if row is None:
            raise RecordingSessionError(f"recording session 不存在: {session_id}")
        return self._decode_session(row)

    async def list_sessions(
        self,
        *,
        room_key: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RecordingSessionRecord]:
        self._validate_page(limit, offset)
        rows = await self.database.fetch_all(
            f"SELECT {', '.join(_SESSION_COLUMNS)} FROM sessions "
            "WHERE room_key = ? AND recording_protocol IS NOT NULL "
            "ORDER BY started_at_ms DESC, id DESC LIMIT ? OFFSET ?",
            (room_key, limit, offset),
        )
        return [self._decode_session(row) for row in rows]

    async def list_segments(
        self,
        *,
        room_key: str,
        session_id: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[RecordingSegmentRecord]:
        self._validate_page(limit, offset)
        selected = session_id
        if selected is None:
            row = await self.database.fetch_one(
                "SELECT id FROM sessions WHERE room_key = ? "
                "AND recording_protocol IS NOT NULL "
                "ORDER BY started_at_ms DESC, id DESC LIMIT 1",
                (room_key,),
            )
            if row is None:
                return []
            selected = str(row["id"])
        else:
            row = await self.database.fetch_one(
                "SELECT id FROM sessions WHERE id = ? AND room_key = ? "
                "AND recording_protocol IS NOT NULL",
                (selected, room_key),
            )
            if row is None:
                raise RecordingSessionError("recording session 不存在或不属于该直播间")
        rows = await self.database.fetch_all(
            f"SELECT {', '.join(_SEGMENT_COLUMNS)} FROM media_files "
            "WHERE session_id = ? ORDER BY sequence, id LIMIT ? OFFSET ?",
            (selected, limit, offset),
        )
        return [self._decode_segment(row) for row in rows]

    async def update_progress(self, session_id: str, snapshot: ProgressSnapshot) -> None:
        await self.database.execute(
            "UPDATE sessions SET last_progress_json = ? "
            "WHERE id = ? AND status = 'active' AND recording_protocol IS NOT NULL",
            (progress_public_json(snapshot), session_id),
        )

    async def sync_segments(
        self,
        *,
        session: RecordingSessionRecord,
        plan: RecordingPlan,
        entries: tuple[SegmentEntry, ...],
    ) -> int:
        rows: list[tuple[object, ...]] = []
        output_root = plan.output_root.expanduser().absolute()
        media_path = plan.media_dir.expanduser().absolute()
        if output_root.is_symlink() or media_path.is_symlink():
            raise RecordingSessionError("录制输出或媒体目录不得是符号链接")
        root = output_root.resolve(strict=True)
        media_dir = media_path.resolve(strict=True)
        try:
            media_dir.relative_to(root)
        except ValueError as exc:
            raise RecordingSessionError("媒体目录越界") from exc
        for entry in entries:
            match = _SEGMENT_NAME_RE.fullmatch(entry.filename)
            if match is None or entry.end_seconds is None:
                continue
            candidate = media_dir / entry.filename
            if candidate.is_symlink() or not candidate.is_file():
                continue
            resolved = candidate.resolve()
            try:
                resolved.relative_to(media_dir)
                relative = resolved.relative_to(root).as_posix()
            except ValueError as exc:
                raise RecordingSessionError("segment 路径越界") from exc
            start_seconds = entry.start_seconds or 0.0
            end_seconds = entry.end_seconds
            if start_seconds < 0 or end_seconds < start_seconds:
                continue
            sequence = int(match.group("sequence"))
            container = match.group("container")
            rows.append(
                (
                    f"{session.id}:{sequence:05d}",
                    session.id,
                    relative,
                    sequence,
                    0,
                    session.started_at_ms + round(start_seconds * 1000),
                    session.started_at_ms + round(start_seconds * 1000),
                    session.started_at_ms + round(end_seconds * 1000),
                    resolved.stat().st_size,
                    "closed",
                    start_seconds,
                    end_seconds,
                    container,
                    f".{container}",
                )
            )
        if not rows:
            return 0

        async def operation(connection: aiosqlite.Connection) -> int:
            for values in rows:
                await connection.execute(
                    """
                    INSERT INTO media_files(
                        id, session_id, relative_path, sequence, continuity_group,
                        requested_start_at_ms, actual_start_at_ms, actual_end_at_ms,
                        size_bytes, status, segment_start_seconds,
                        segment_end_seconds, container, media_suffix
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, relative_path) DO UPDATE SET
                        actual_start_at_ms = excluded.actual_start_at_ms,
                        actual_end_at_ms = excluded.actual_end_at_ms,
                        size_bytes = excluded.size_bytes,
                        segment_start_seconds = excluded.segment_start_seconds,
                        segment_end_seconds = excluded.segment_end_seconds,
                        container = excluded.container,
                        media_suffix = excluded.media_suffix,
                        status = CASE
                            WHEN media_files.status IN ('verified', 'quarantined')
                                THEN media_files.status
                            ELSE 'closed'
                        END
                    """,
                    values,
                )
            return len(rows)

        return await self.database.write(operation)

    async def record_result(
        self,
        *,
        session_id: str,
        result: RecorderResult | None,
        error_code: str | None = None,
    ) -> None:
        await self.database.execute(
            """
            UPDATE sessions
            SET ffmpeg_returncode = ?, stop_stage = ?, last_progress_json = ?,
                recording_error_code = ?
            WHERE id = ? AND recording_protocol IS NOT NULL
            """,
            (
                result.returncode if result else None,
                result.stop_stage if result else None,
                progress_public_json(result.last_progress if result else None),
                error_code[:120] if error_code else None,
                session_id,
            ),
        )

    async def recover_interrupted(
        self,
        *,
        runtime_instance_id: str,
        at_ms: int,
    ) -> list[str]:
        async def operation(connection: aiosqlite.Connection) -> list[str]:
            cursor = await connection.execute(
                "SELECT id FROM sessions WHERE status = 'active' AND recording_protocol IS NOT NULL"
            )
            rows = await cursor.fetchall()
            await cursor.close()
            session_ids = [str(row["id"]) for row in rows]
            for session_id in session_ids:
                await connection.execute(
                    """
                    UPDATE recipient_intervals
                    SET ended_at_ms = ?, ended_monotonic_ns = NULL,
                        ended_runtime_instance_id = ?
                    WHERE session_id = ? AND ended_at_ms IS NULL
                    """,
                    (at_ms, runtime_instance_id, session_id),
                )
                await connection.execute(
                    """
                    UPDATE sessions
                    SET status = 'interrupted', ended_at_ms = ?,
                        end_reason = 'app_restart_recovery',
                        ended_monotonic_ns = NULL,
                        ended_runtime_instance_id = ?,
                        recording_error_code = COALESCE(
                            recording_error_code, 'app_restart_recovery'
                        )
                    WHERE id = ? AND status = 'active'
                    """,
                    (at_ms, runtime_instance_id, session_id),
                )
            return session_ids

        return await self.database.write(operation)

    @staticmethod
    def _validate_page(limit: int, offset: int) -> None:
        if not 1 <= limit <= 500 or not 0 <= offset <= 1_000_000:
            raise ValueError("分页参数超出允许范围")

    @staticmethod
    def _decode_session(row: dict[str, Any]) -> RecordingSessionRecord:
        try:
            query_keys = json.loads(str(row.get("input_query_keys_json") or "[]"))
        except json.JSONDecodeError:
            query_keys = []
        if not isinstance(query_keys, list):
            query_keys = []
        try:
            last_progress = json.loads(str(row.get("last_progress_json") or "{}"))
        except json.JSONDecodeError:
            last_progress = {}
        if not isinstance(last_progress, dict):
            last_progress = {}
        return RecordingSessionRecord(
            id=str(row["id"]),
            room_key=str(row["room_key"]),
            external_room_id=(
                str(row["external_room_id"]) if row["external_room_id"] is not None else None
            ),
            title=str(row["title"] or ""),
            status=str(row["status"]),
            started_at_ms=int(row["started_at_ms"]),
            ended_at_ms=int(row["ended_at_ms"]) if row["ended_at_ms"] is not None else None,
            end_reason=str(row["end_reason"]) if row["end_reason"] is not None else None,
            runtime_instance_id=(
                str(row["runtime_instance_id"]) if row["runtime_instance_id"] is not None else None
            ),
            recording_protocol=(
                str(row["recording_protocol"]) if row["recording_protocol"] is not None else None
            ),
            recording_quality=(
                str(row["recording_quality"]) if row["recording_quality"] is not None else None
            ),
            input_host=str(row["input_host"] or ""),
            input_path_sha256=str(row["input_path_sha256"] or ""),
            input_url_sha256=str(row["input_url_sha256"] or ""),
            input_query_keys=tuple(str(item) for item in query_keys[:128]),
            recording_container=str(row["recording_container"] or "mkv"),
            segment_seconds=(
                int(row["segment_seconds"]) if row["segment_seconds"] is not None else None
            ),
            ffmpeg_returncode=(
                int(row["ffmpeg_returncode"]) if row["ffmpeg_returncode"] is not None else None
            ),
            stop_stage=str(row["stop_stage"]) if row["stop_stage"] is not None else None,
            last_progress=last_progress,
            recording_error_code=(
                str(row["recording_error_code"])
                if row["recording_error_code"] is not None
                else None
            ),
        )

    @staticmethod
    def _decode_segment(row: dict[str, Any]) -> RecordingSegmentRecord:
        return RecordingSegmentRecord(
            id=str(row["id"]),
            session_id=str(row["session_id"]),
            relative_path=str(row["relative_path"]),
            sequence=int(row["sequence"]),
            continuity_group=int(row["continuity_group"]),
            requested_start_at_ms=int(row["requested_start_at_ms"]),
            actual_start_at_ms=(
                int(row["actual_start_at_ms"]) if row["actual_start_at_ms"] is not None else None
            ),
            actual_end_at_ms=(
                int(row["actual_end_at_ms"]) if row["actual_end_at_ms"] is not None else None
            ),
            size_bytes=int(row["size_bytes"]) if row["size_bytes"] is not None else None,
            status=str(row["status"]),
            segment_start_seconds=(
                float(row["segment_start_seconds"])
                if row["segment_start_seconds"] is not None
                else None
            ),
            segment_end_seconds=(
                float(row["segment_end_seconds"])
                if row["segment_end_seconds"] is not None
                else None
            ),
            container=str(row["container"] or ""),
            media_suffix=str(row["media_suffix"] or ""),
        )

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.db import Database
from app.postprocess.models import ExportOutputPlan, ExportPlan, ExportSource

_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]+")

class ExportPlanError(RuntimeError):
    """Raised when a recording cannot produce a safe deterministic export plan."""


@dataclass(frozen=True, slots=True)
class _Session:
    id: str
    room_key: str
    status: str
    started_at_ms: int
    ended_at_ms: int | None


def _safe_token(value: str, *, fallback: str) -> str:
    token = _SAFE_TOKEN_RE.sub("-", value).strip("-._")
    return (token or fallback)[:80]


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ExportPlanner:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def build(self, session_id: str) -> ExportPlan:
        session_row = await self.database.fetch_one(
            "SELECT id, room_key, status, started_at_ms, ended_at_ms "
            "FROM sessions WHERE id = ? AND recording_protocol IS NOT NULL",
            (session_id,),
        )
        if session_row is None:
            raise ExportPlanError("recording session 不存在")
        session = _Session(
            id=str(session_row["id"]),
            room_key=str(session_row["room_key"]),
            status=str(session_row["status"]),
            started_at_ms=int(session_row["started_at_ms"]),
            ended_at_ms=(
                int(session_row["ended_at_ms"])
                if session_row["ended_at_ms"] is not None
                else None
            ),
        )
        if session.status != "ended" or session.ended_at_ms is None:
            raise ExportPlanError("仅允许导出已正常结束的 recording session")

        media_rows = await self.database.fetch_all(
            "SELECT id, relative_path, segment_start_seconds, segment_end_seconds "
            "FROM media_files WHERE session_id = ? "
            "AND status IN ('closed', 'verified', 'recovered') "
            "AND segment_start_seconds IS NOT NULL AND segment_end_seconds IS NOT NULL "
            "ORDER BY segment_start_seconds, sequence, id",
            (session_id,),
        )
        sources = tuple(
            ExportSource(
                media_id=str(row["id"]),
                relative_path=str(row["relative_path"]),
                start_seconds=float(row["segment_start_seconds"]),
                end_seconds=float(row["segment_end_seconds"]),
            )
            for row in media_rows
            if float(row["segment_end_seconds"]) > float(row["segment_start_seconds"])
        )
        if not sources:
            raise ExportPlanError("recording session 没有可导出的闭合媒体分片")

        media_start = min(item.start_seconds for item in sources)
        media_end = max(item.end_seconds for item in sources)
        interval_rows = await self.database.fetch_all(
            "SELECT id, status, reason, recipient_key, started_at_ms, ended_at_ms "
            "FROM recipient_intervals WHERE session_id = ? AND ended_at_ms IS NOT NULL "
            "ORDER BY started_at_ms, id",
            (session_id,),
        )
        outputs: list[ExportOutputPlan] = []
        room_token = _safe_token(session.room_key, fallback="room")
        session_token = _safe_token(session.id, fallback="session")
        for row in interval_rows:
            interval_id = int(row["id"])
            interval_status = str(row["status"])
            start = max(
                media_start,
                (int(row["started_at_ms"]) - session.started_at_ms) / 1000.0,
            )
            end = min(
                media_end,
                (int(row["ended_at_ms"]) - session.started_at_ms) / 1000.0,
            )
            if end - start <= 0.001:
                continue
            selected = tuple(
                item for item in sources if item.end_seconds > start and item.start_seconds < end
            )
            if not selected:
                continue
            recipient_key = str(row["recipient_key"] or "")
            recipient_hash = (
                hashlib.sha256(recipient_key.encode("utf-8")).hexdigest()
                if recipient_key
                else ""
            )
            identity = recipient_hash[:12] if recipient_hash else "none"
            filename = f"{interval_id:08d}-{interval_status}-{identity}.mkv"
            relative_path = (
                f"exports/{room_token}/{session_token}/{filename}"
            )
            first_start = selected[0].start_seconds
            outputs.append(
                ExportOutputPlan(
                    interval_id=interval_id,
                    interval_status=interval_status,
                    interval_reason=(str(row["reason"]) if row["reason"] is not None else None),
                    recipient_key_sha256=recipient_hash,
                    sources=selected,
                    relative_path=relative_path,
                    trim_start_seconds=round(max(0.0, start - first_start), 6),
                    duration_seconds=round(end - start, 6),
                )
            )
        if not outputs:
            raise ExportPlanError("recording session 没有与媒体相交的闭合 recipient interval")

        key_payload = {
            "session_id": session.id,
            "outputs": [item.to_dict() for item in outputs],
            "planner_version": 1,
        }
        return ExportPlan(
            session_id=session.id,
            room_key=session.room_key,
            idempotency_key=_canonical_sha256(key_payload),
            outputs=tuple(outputs),
        )

from __future__ import annotations

import json
from typing import Any

from app.sessions.models import RecipientEventRecord, RecipientIntervalRecord
from app.sessions.repository_errors import RecipientSessionStateError

EVENT_PUBLIC_COLUMNS = (
    "id",
    "session_id",
    "dedup_key",
    "msg_id",
    "envelope_msg_id",
    "server_event_at_ms",
    "server_time_unit",
    "received_at_ms",
    "received_monotonic_ns",
    "runtime_instance_id",
    "recipient_user_id",
    "recipient_user_open_id",
    "recipient_key",
    "change_reason_enum",
    "payload_hash",
    "payload_size",
    "duplicate_count",
    "first_received_at_ms",
    "last_received_at_ms",
    "is_late",
)

INTERVAL_COLUMNS = (
    "id",
    "session_id",
    "status",
    "reason",
    "recipient_key",
    "recipient_user_id",
    "recipient_user_open_id",
    "started_at_ms",
    "ended_at_ms",
    "started_monotonic_ns",
    "ended_monotonic_ns",
    "runtime_instance_id",
    "ended_runtime_instance_id",
    "start_event_id",
    "end_event_id",
)


def validate_raw_payload_json(raw_payload_json: str) -> None:
    if len(raw_payload_json.encode("utf-8")) > 1_048_576:
        raise RecipientSessionStateError("raw payload 超过 1 MiB 上限")
    try:
        json.loads(raw_payload_json)
    except json.JSONDecodeError as exc:
        raise RecipientSessionStateError("raw_payload_json 必须是有效 JSON") from exc


def validate_page(*, limit: int, offset: int) -> None:
    if not 1 <= limit <= 500 or not 0 <= offset <= 1_000_000:
        raise ValueError("分页参数超出允许范围")


def decode_event(row: dict[str, Any]) -> RecipientEventRecord:
    return RecipientEventRecord(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        dedup_key=str(row["dedup_key"]),
        msg_id=str(row["msg_id"]) if row["msg_id"] is not None else None,
        envelope_msg_id=(
            str(row["envelope_msg_id"]) if row["envelope_msg_id"] is not None else None
        ),
        server_event_at_ms=(
            int(row["server_event_at_ms"])
            if row["server_event_at_ms"] is not None
            else None
        ),
        server_time_unit=(
            str(row["server_time_unit"]) if row["server_time_unit"] is not None else None
        ),
        received_at_ms=int(row["received_at_ms"]),
        received_monotonic_ns=int(row["received_monotonic_ns"]),
        runtime_instance_id=str(row["runtime_instance_id"]),
        recipient_user_id=(
            str(row["recipient_user_id"]) if row["recipient_user_id"] is not None else None
        ),
        recipient_user_open_id=(
            str(row["recipient_user_open_id"])
            if row["recipient_user_open_id"] is not None
            else None
        ),
        recipient_key=(
            str(row["recipient_key"]) if row["recipient_key"] is not None else None
        ),
        change_reason_enum=(
            int(row["change_reason_enum"])
            if row["change_reason_enum"] is not None
            else None
        ),
        payload_hash=str(row["payload_hash"]),
        payload_size=int(row["payload_size"]),
        duplicate_count=int(row["duplicate_count"]),
        first_received_at_ms=int(row["first_received_at_ms"]),
        last_received_at_ms=int(row["last_received_at_ms"]),
        is_late=bool(row["is_late"]),
    )


def decode_interval(row: dict[str, Any]) -> RecipientIntervalRecord:
    status = str(row["status"])
    if status not in {"waiting", "active", "unknown"}:
        raise RecipientSessionStateError("数据库包含非法 recipient interval status")
    return RecipientIntervalRecord(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        status=status,  # type: ignore[arg-type]
        reason=str(row["reason"]) if row["reason"] is not None else None,
        recipient_key=(
            str(row["recipient_key"]) if row["recipient_key"] is not None else None
        ),
        recipient_user_id=(
            str(row["recipient_user_id"]) if row["recipient_user_id"] is not None else None
        ),
        recipient_user_open_id=(
            str(row["recipient_user_open_id"])
            if row["recipient_user_open_id"] is not None
            else None
        ),
        started_at_ms=int(row["started_at_ms"]),
        ended_at_ms=int(row["ended_at_ms"]) if row["ended_at_ms"] is not None else None,
        started_monotonic_ns=int(row["started_monotonic_ns"]),
        ended_monotonic_ns=(
            int(row["ended_monotonic_ns"])
            if row["ended_monotonic_ns"] is not None
            else None
        ),
        runtime_instance_id=str(row["runtime_instance_id"]),
        ended_runtime_instance_id=(
            str(row["ended_runtime_instance_id"])
            if row["ended_runtime_instance_id"] is not None
            else None
        ),
        start_event_id=(
            int(row["start_event_id"]) if row["start_event_id"] is not None else None
        ),
        end_event_id=int(row["end_event_id"]) if row["end_event_id"] is not None else None,
    )

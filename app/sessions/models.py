from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

IntervalStatus = Literal["waiting", "active", "unknown"]
ProjectionOutcome = Literal[
    "active",
    "duplicate",
    "late",
    "same-recipient",
    "same-unknown",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class RecipientEventRecord:
    id: int
    session_id: str
    dedup_key: str
    msg_id: str | None
    envelope_msg_id: str | None
    server_event_at_ms: int | None
    server_time_unit: str | None
    received_at_ms: int
    received_monotonic_ns: int
    runtime_instance_id: str
    recipient_user_id: str | None
    recipient_user_open_id: str | None
    recipient_key: str | None
    change_reason_enum: int | None
    payload_hash: str
    payload_size: int
    duplicate_count: int
    first_received_at_ms: int
    last_received_at_ms: int
    is_late: bool

    def to_public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RecipientIntervalRecord:
    id: int
    session_id: str
    status: IntervalStatus
    reason: str | None
    recipient_key: str | None
    recipient_user_id: str | None
    recipient_user_open_id: str | None
    started_at_ms: int
    ended_at_ms: int | None
    started_monotonic_ns: int
    ended_monotonic_ns: int | None
    runtime_instance_id: str
    ended_runtime_instance_id: str | None
    start_event_id: int | None
    end_event_id: int | None

    def to_public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RecipientSessionState:
    room_key: str
    session_id: str | None
    session_status: str | None
    interval: RecipientIntervalRecord | None
    protocol_contract_sha256: str | None
    protocol_live_verified: bool

    def to_public_dict(self) -> dict[str, object]:
        return {
            "room_key": self.room_key,
            "session_id": self.session_id,
            "session_status": self.session_status,
            "current": self.interval.to_public_dict() if self.interval else None,
            "protocol_contract_sha256": self.protocol_contract_sha256,
            "protocol_live_verified": self.protocol_live_verified,
        }


@dataclass(frozen=True, slots=True)
class RecipientProjectionResult:
    outcome: ProjectionOutcome
    event: RecipientEventRecord
    state: RecipientSessionState

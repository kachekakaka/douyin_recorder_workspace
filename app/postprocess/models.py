from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

JobStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]
OutputStatus = Literal["queued", "writing", "succeeded", "failed", "canceled"]


@dataclass(frozen=True, slots=True)
class ExportSource:
    media_id: str
    relative_path: str
    start_seconds: float
    end_seconds: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExportOutputPlan:
    interval_id: int
    interval_status: str
    interval_reason: str | None
    recipient_key_sha256: str
    sources: tuple[ExportSource, ...]
    relative_path: str
    trim_start_seconds: float
    duration_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "interval_id": self.interval_id,
            "interval_status": self.interval_status,
            "interval_reason": self.interval_reason,
            "recipient_key_sha256": self.recipient_key_sha256,
            "sources": [item.to_dict() for item in self.sources],
            "relative_path": self.relative_path,
            "trim_start_seconds": self.trim_start_seconds,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class ExportPlan:
    session_id: str
    room_key: str
    idempotency_key: str
    outputs: tuple[ExportOutputPlan, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "room_key": self.room_key,
            "idempotency_key": self.idempotency_key,
            "outputs": [item.to_dict() for item in self.outputs],
        }


@dataclass(frozen=True, slots=True)
class PostprocessOutputRecord:
    id: str
    job_id: str
    interval_id: int
    interval_status: str
    interval_reason: str | None
    recipient_key_sha256: str
    source_media_ids: tuple[str, ...]
    relative_path: str
    trim_start_seconds: float
    duration_seconds: float
    status: OutputStatus
    size_bytes: int | None
    sha256: str | None
    created_at_ms: int
    updated_at_ms: int

    def to_public_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["source_media_ids"] = list(self.source_media_ids)
        return value


@dataclass(frozen=True, slots=True)
class PostprocessJobRecord:
    id: str
    job_type: str
    session_id: str
    status: JobStatus
    priority: int
    attempts: int
    max_attempts: int
    idempotency_key: str
    cancel_requested: bool
    error_code: str | None
    created_at_ms: int
    updated_at_ms: int
    started_at_ms: int | None
    ended_at_ms: int | None
    outputs: tuple[PostprocessOutputRecord, ...] = ()

    def to_public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "job_type": self.job_type,
            "session_id": self.session_id,
            "status": self.status,
            "priority": self.priority,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "cancel_requested": self.cancel_requested,
            "error_code": self.error_code,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "outputs": [item.to_public_dict() for item in self.outputs],
        }

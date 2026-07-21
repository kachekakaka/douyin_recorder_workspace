from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from app.media import ProgressSnapshot


@dataclass(frozen=True, slots=True)
class RecordingSessionRecord:
    id: str
    room_key: str
    external_room_id: str | None
    title: str
    status: str
    started_at_ms: int
    ended_at_ms: int | None
    end_reason: str | None
    runtime_instance_id: str | None
    recording_protocol: str | None
    recording_quality: str | None
    input_host: str
    input_path_sha256: str
    input_url_sha256: str
    input_query_keys: tuple[str, ...]
    recording_container: str
    segment_seconds: int | None
    ffmpeg_returncode: int | None
    stop_stage: str | None
    last_progress: dict[str, object]
    recording_error_code: str | None

    def to_public_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["input_query_keys"] = list(self.input_query_keys)
        return value


@dataclass(frozen=True, slots=True)
class RecordingSegmentRecord:
    id: str
    session_id: str
    relative_path: str
    sequence: int
    continuity_group: int
    requested_start_at_ms: int
    actual_start_at_ms: int | None
    actual_end_at_ms: int | None
    size_bytes: int | None
    status: str
    segment_start_seconds: float | None
    segment_end_seconds: float | None
    container: str
    media_suffix: str

    def to_public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RecordingState:
    room_key: str
    active: bool
    session: RecordingSessionRecord | None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "room_key": self.room_key,
            "active": self.active,
            "session": self.session.to_public_dict() if self.session else None,
        }


def progress_public_json(snapshot: ProgressSnapshot | None) -> str:
    if snapshot is None:
        return "{}"
    value = {
        "received_at_ms": snapshot.received_at_ms,
        "frame": snapshot.frame,
        "fps": snapshot.fps,
        "total_size": snapshot.total_size,
        "out_time_us": snapshot.out_time_us,
        "speed": snapshot.speed,
        "progress": snapshot.progress,
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

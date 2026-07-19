from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        payload = f"{self.version}\n{self.name}\n{self.sql}".encode()
        return hashlib.sha256(payload).hexdigest()


MIGRATIONS = (
    Migration(
        1,
        "p0_initial_schema",
        r"""
CREATE TABLE IF NOT EXISTS runtime_instances (
    id TEXT PRIMARY KEY,
    app_version TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    ended_at_ms INTEGER NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    room_key TEXT PRIMARY KEY,
    room_url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    quality TEXT NOT NULL DEFAULT 'origin',
    protocol TEXT NOT NULL DEFAULT 'flv',
    poll_interval_seconds INTEGER NOT NULL DEFAULT 15 CHECK (poll_interval_seconds >= 5),
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    room_key TEXT NOT NULL REFERENCES rooms(room_key),
    external_room_id TEXT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('active', 'ended', 'interrupted', 'failed')),
    started_at_ms INTEGER NOT NULL,
    ended_at_ms INTEGER NULL,
    end_reason TEXT NULL,
    runtime_instance_id TEXT NULL REFERENCES runtime_instances(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_session_per_room
    ON sessions(room_key) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS recipient_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    dedup_key TEXT NOT NULL,
    msg_id TEXT NULL,
    server_event_at_ms INTEGER NULL,
    received_at_ms INTEGER NOT NULL,
    received_monotonic_ns INTEGER NOT NULL,
    runtime_instance_id TEXT NOT NULL,
    recipient_user_id TEXT NULL,
    recipient_user_open_id TEXT NULL,
    recipient_key TEXT NULL,
    change_reason_enum INTEGER NULL,
    extra_json TEXT NOT NULL DEFAULT '{}',
    raw_payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    first_received_at_ms INTEGER NOT NULL,
    last_received_at_ms INTEGER NOT NULL,
    is_late INTEGER NOT NULL DEFAULT 0 CHECK (is_late IN (0, 1))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_event_dedup
    ON recipient_events(session_id, dedup_key);

CREATE TABLE IF NOT EXISTS recipient_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    status TEXT NOT NULL CHECK (status IN ('waiting', 'active', 'unknown')),
    reason TEXT NULL,
    recipient_key TEXT NULL,
    recipient_user_id TEXT NULL,
    recipient_user_open_id TEXT NULL,
    started_at_ms INTEGER NOT NULL,
    ended_at_ms INTEGER NULL,
    started_monotonic_ns INTEGER NOT NULL,
    ended_monotonic_ns INTEGER NULL,
    runtime_instance_id TEXT NOT NULL,
    start_event_id INTEGER NULL REFERENCES recipient_events(id),
    end_event_id INTEGER NULL REFERENCES recipient_events(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_open_interval_per_session
    ON recipient_intervals(session_id) WHERE ended_at_ms IS NULL;

CREATE TABLE IF NOT EXISTS media_files (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    relative_path TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    continuity_group INTEGER NOT NULL DEFAULT 0,
    requested_start_at_ms INTEGER NOT NULL,
    actual_start_at_ms INTEGER NULL,
    actual_end_at_ms INTEGER NULL,
    first_pts TEXT NULL,
    last_pts TEXT NULL,
    time_base TEXT NULL,
    codec_signature TEXT NULL,
    size_bytes INTEGER NULL,
    status TEXT NOT NULL CHECK (
        status IN ('writing', 'closed', 'verified', 'recovered', 'quarantined')
    ),
    UNIQUE(session_id, relative_path)
);

CREATE TABLE IF NOT EXISTS media_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    gap_start_ms INTEGER NOT NULL,
    gap_end_ms INTEGER NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recorder_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_key TEXT NULL,
    session_id TEXT NULL,
    runtime_instance_id TEXT NOT NULL,
    component TEXT NOT NULL,
    event_type TEXT NOT NULL,
    code TEXT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'success', 'failed', 'cancelled')
    ),
    priority INTEGER NOT NULL DEFAULT 100,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    not_before_ms INTEGER NOT NULL,
    lease_owner TEXT NULL,
    lease_expires_at_ms INTEGER NULL,
    idempotency_key TEXT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    result_json TEXT NULL,
    last_error TEXT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    interval_id INTEGER NULL REFERENCES recipient_intervals(id),
    source_media_ids_json TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size_bytes INTEGER NULL,
    sha256 TEXT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    remote_addr TEXT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS protocol_probe_runs (
    id TEXT PRIMARY KEY,
    room_url TEXT NULL,
    websocket_host TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    ended_at_ms INTEGER NULL,
    runtime_instance_id TEXT NOT NULL,
    frame_count INTEGER NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    target_message_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    report_path TEXT NULL
);
""",
    ),
    Migration(
        2,
        "p1a_room_checks",
        r"""
CREATE TABLE IF NOT EXISTS room_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_key TEXT NOT NULL REFERENCES rooms(room_key) ON DELETE CASCADE,
    checked_at_ms INTEGER NOT NULL,
    live_state TEXT NOT NULL CHECK (
        live_state IN ('live', 'offline', 'unknown', 'blocked', 'error')
    ),
    http_status INTEGER NULL,
    final_host TEXT NOT NULL DEFAULT '',
    final_path TEXT NOT NULL DEFAULT '/',
    external_room_id TEXT NULL,
    web_rid TEXT NULL,
    title TEXT NOT NULL DEFAULT '',
    stream_candidate_count INTEGER NOT NULL DEFAULT 0 CHECK (stream_candidate_count >= 0),
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_room_checks_room_time
    ON room_checks(room_key, checked_at_ms DESC);
""",
    ),
    Migration(
        3,
        "p1a_room_url_uniqueness",
        r"""
CREATE UNIQUE INDEX IF NOT EXISTS uq_rooms_room_url
    ON rooms(room_url);
CREATE INDEX IF NOT EXISTS ix_room_checks_room_id
    ON room_checks(room_key, id DESC);
""",
    ),
    Migration(
        4,
        "p1b_recipient_persistence_fields",
        r"""
ALTER TABLE sessions ADD COLUMN protocol_contract_sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE sessions ADD COLUMN protocol_live_verified INTEGER NOT NULL DEFAULT 0
    CHECK (protocol_live_verified IN (0, 1));
ALTER TABLE sessions ADD COLUMN started_monotonic_ns INTEGER NULL;
ALTER TABLE sessions ADD COLUMN ended_monotonic_ns INTEGER NULL;
ALTER TABLE sessions ADD COLUMN ended_runtime_instance_id TEXT NULL
    REFERENCES runtime_instances(id);

ALTER TABLE recipient_events ADD COLUMN envelope_msg_id TEXT NULL;
ALTER TABLE recipient_events ADD COLUMN server_time_unit TEXT NULL;
ALTER TABLE recipient_events ADD COLUMN payload_size INTEGER NOT NULL DEFAULT 0
    CHECK (payload_size >= 0);
ALTER TABLE recipient_events ADD COLUMN unknown_fields_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE recipient_intervals ADD COLUMN ended_runtime_instance_id TEXT NULL
    REFERENCES runtime_instances(id);

CREATE INDEX IF NOT EXISTS ix_recipient_events_session_received
    ON recipient_events(session_id, received_at_ms, id);
CREATE INDEX IF NOT EXISTS ix_recipient_intervals_session_started
    ON recipient_intervals(session_id, started_at_ms, id);
CREATE INDEX IF NOT EXISTS ix_sessions_room_started
    ON sessions(room_key, started_at_ms DESC);
""",
    ),
)

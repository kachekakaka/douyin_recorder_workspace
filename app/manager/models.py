from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class WorkerState:
    room_key: str
    configured: bool
    running: bool
    lifecycle: str
    last_live_state: str
    consecutive_offline: int
    consecutive_errors: int
    last_checked_at_ms: int | None
    next_check_at_ms: int | None
    last_error_code: str
    recording_active: bool

    def to_public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ManagerState:
    enabled: bool
    running: bool
    max_parallel_checks: int
    worker_count: int
    workers: tuple[WorkerState, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "max_parallel_checks": self.max_parallel_checks,
            "worker_count": self.worker_count,
            "workers": [item.to_public_dict() for item in self.workers],
        }

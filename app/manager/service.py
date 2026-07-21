from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field

from app.manager.models import ManagerState, WorkerState
from app.recording import (
    RecordingAlreadyActiveError,
    RecordingSessionError,
    SingleRoomRecordingService,
)
from app.rooms import RoomNotFoundError, RoomService

_ERROR_CODE_RE = re.compile(r"[^a-z0-9_]+")
_MAX_ERROR_BACKOFF_SECONDS = 300.0


@dataclass(slots=True)
class _WorkerRuntime:
    room_key: str
    task: asyncio.Task[None] | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    wake_event: asyncio.Event = field(default_factory=asyncio.Event)
    lifecycle: str = "starting"
    last_live_state: str = "unknown"
    consecutive_offline: int = 0
    consecutive_errors: int = 0
    last_checked_at_ms: int | None = None
    next_check_at_ms: int | None = None
    last_error_code: str = ""
    recording_active: bool = False
    next_delay_seconds: float = 0.0

    def snapshot(self, *, configured: bool = True) -> WorkerState:
        return WorkerState(
            room_key=self.room_key,
            configured=configured,
            running=self.task is not None and not self.task.done(),
            lifecycle=self.lifecycle,
            last_live_state=self.last_live_state,
            consecutive_offline=self.consecutive_offline,
            consecutive_errors=self.consecutive_errors,
            last_checked_at_ms=self.last_checked_at_ms,
            next_check_at_ms=self.next_check_at_ms,
            last_error_code=self.last_error_code,
            recording_active=self.recording_active,
        )


class RoomManager:
    def __init__(
        self,
        *,
        room_service: RoomService,
        recording_service: SingleRoomRecordingService,
        enabled: bool,
        jitter_seconds: int,
        offline_confirmations: int,
        max_parallel_checks: int,
        wall_time_ms: Callable[[], int] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if not 0 <= jitter_seconds <= 300:
            raise ValueError("jitter_seconds 必须在 0–300 之间")
        if not 1 <= offline_confirmations <= 20:
            raise ValueError("offline_confirmations 必须在 1–20 之间")
        if not 1 <= max_parallel_checks <= 100:
            raise ValueError("max_parallel_checks 必须在 1–100 之间")
        self.room_service = room_service
        self.recording_service = recording_service
        self.enabled = enabled
        self.jitter_seconds = jitter_seconds
        self.offline_confirmations = offline_confirmations
        self.max_parallel_checks = max_parallel_checks
        self.wall_time_ms = wall_time_ms or (lambda: int(time.time() * 1000))
        self.monotonic = monotonic
        self.random_uniform = random_uniform
        self._check_semaphore = asyncio.Semaphore(max_parallel_checks)
        self._workers: dict[str, _WorkerRuntime] = {}
        self._reconcile_lock = asyncio.Lock()
        self._running = False
        self._closed = False

    async def start(self) -> ManagerState:
        if self._closed:
            raise RuntimeError("RoomManager 已关闭")
        if not self.enabled:
            return await self.get_status()
        async with self._reconcile_lock:
            if not self._running:
                self._running = True
            await self._reconcile_locked()
        return await self.get_status()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._running = False
        async with self._reconcile_lock:
            workers = list(self._workers.values())
            self._workers.clear()
        await asyncio.gather(
            *(self._stop_worker(runtime, stop_recording=False) for runtime in workers),
            return_exceptions=True,
        )

    async def reconcile(self) -> ManagerState:
        if self._closed:
            return await self.get_status()
        if self.enabled and not self._running:
            return await self.start()
        async with self._reconcile_lock:
            await self._reconcile_locked()
        return await self.get_status()

    async def get_status(self) -> ManagerState:
        workers = tuple(
            runtime.snapshot()
            for runtime in sorted(self._workers.values(), key=lambda item: item.room_key)
        )
        return ManagerState(
            enabled=self.enabled,
            running=self._running,
            max_parallel_checks=self.max_parallel_checks,
            worker_count=len(workers),
            workers=workers,
        )

    async def get_worker(self, room_key: str) -> WorkerState:
        room = await self.room_service.get_room(room_key)
        runtime = self._workers.get(room_key)
        if runtime is None:
            recording = await self.recording_service.get_state(room_key)
            return WorkerState(
                room_key=room_key,
                configured=room.enabled,
                running=False,
                lifecycle="disabled" if not room.enabled else "stopped",
                last_live_state="unknown",
                consecutive_offline=0,
                consecutive_errors=0,
                last_checked_at_ms=None,
                next_check_at_ms=None,
                last_error_code="",
                recording_active=recording.active,
            )
        return runtime.snapshot(configured=room.enabled)

    async def run_once(self, room_key: str) -> WorkerState:
        runtime = self._workers.setdefault(
            room_key,
            _WorkerRuntime(room_key=room_key),
        )
        await self._run_iteration(runtime)
        return runtime.snapshot()

    async def _reconcile_locked(self) -> None:
        if not self.enabled or not self._running:
            return
        rooms = await self.room_service.list_rooms()
        desired = {room.room_key for room in rooms if room.enabled}
        removed = [
            runtime for room_key, runtime in self._workers.items() if room_key not in desired
        ]
        for runtime in removed:
            self._workers.pop(runtime.room_key, None)
        if removed:
            await asyncio.gather(
                *(self._stop_worker(runtime, stop_recording=True) for runtime in removed),
                return_exceptions=True,
            )
        for room in rooms:
            if not room.enabled:
                continue
            runtime = self._workers.get(room.room_key)
            if runtime is None:
                runtime = _WorkerRuntime(room_key=room.room_key)
                self._workers[room.room_key] = runtime
            if runtime.task is None or runtime.task.done():
                runtime.stop_event = asyncio.Event()
                runtime.wake_event = asyncio.Event()
                runtime.task = asyncio.create_task(
                    self._worker_loop(runtime),
                    name=f"room-worker-{room.room_key}",
                )
            else:
                runtime.wake_event.set()

    async def _stop_worker(
        self,
        runtime: _WorkerRuntime,
        *,
        stop_recording: bool,
    ) -> None:
        runtime.stop_event.set()
        runtime.wake_event.set()
        task = runtime.task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        runtime.task = None
        runtime.lifecycle = "stopped"
        runtime.next_check_at_ms = None
        if stop_recording:
            with suppress(RoomNotFoundError, RecordingSessionError):
                await self.recording_service.stop_recording(
                    runtime.room_key,
                    reason="room_disabled",
                )
            with suppress(RoomNotFoundError):
                runtime.recording_active = (
                    await self.recording_service.get_state(runtime.room_key)
                ).active

    async def _worker_loop(self, runtime: _WorkerRuntime) -> None:
        try:
            while not runtime.stop_event.is_set():
                runtime.wake_event.clear()
                await self._run_iteration(runtime)
                if runtime.stop_event.is_set():
                    break
                timeout = max(0.05, runtime.next_delay_seconds)
                try:
                    await asyncio.wait_for(runtime.wake_event.wait(), timeout=timeout)
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            runtime.lifecycle = "stopped"
            runtime.next_check_at_ms = None

    async def _run_iteration(self, runtime: _WorkerRuntime) -> None:
        try:
            room = await self.room_service.get_room(runtime.room_key)
            if not room.enabled:
                runtime.lifecycle = "disabled"
                runtime.next_delay_seconds = float(room.poll_interval_seconds)
                runtime.next_check_at_ms = None
                return
            runtime.lifecycle = "checking"
            async with self._check_semaphore:
                result = await self.room_service.check_room(runtime.room_key)
            checked_at_ms = self.wall_time_ms()
            runtime.last_checked_at_ms = checked_at_ms
            live_state = str(result.snapshot.live_state or "unknown")
            runtime.last_live_state = live_state
            if live_state == "live":
                runtime.consecutive_offline = 0
                runtime.consecutive_errors = 0
                runtime.last_error_code = ""
                state = await self.recording_service.get_state(runtime.room_key)
                if not state.active:
                    try:
                        state = await self.recording_service.start_recording(runtime.room_key)
                    except RecordingAlreadyActiveError:
                        state = await self.recording_service.get_state(runtime.room_key)
                runtime.recording_active = state.active
                runtime.lifecycle = "recording" if state.active else "live"
                delay = self._normal_delay(room.poll_interval_seconds)
            elif live_state == "offline":
                runtime.consecutive_errors = 0
                runtime.last_error_code = ""
                runtime.consecutive_offline += 1
                state = await self.recording_service.get_state(runtime.room_key)
                if state.active and runtime.consecutive_offline >= self.offline_confirmations:
                    state = await self.recording_service.stop_recording(
                        runtime.room_key,
                        reason="confirmed_offline",
                    )
                runtime.recording_active = state.active
                runtime.lifecycle = "offline_pending" if state.active else "offline"
                delay = self._normal_delay(room.poll_interval_seconds)
            else:
                runtime.consecutive_offline = 0
                runtime.consecutive_errors += 1
                runtime.last_error_code = f"live_state_{self._safe_code(live_state)}"
                state = await self.recording_service.get_state(runtime.room_key)
                runtime.recording_active = state.active
                runtime.lifecycle = (
                    live_state
                    if live_state in {"unknown", "blocked", "error"}
                    else "unknown"
                )
                delay = self._error_delay(room.poll_interval_seconds, runtime.consecutive_errors)
            runtime.next_delay_seconds = delay
            runtime.next_check_at_ms = checked_at_ms + int(delay * 1000)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            runtime.last_checked_at_ms = self.wall_time_ms()
            runtime.last_live_state = "error"
            runtime.consecutive_offline = 0
            runtime.consecutive_errors += 1
            runtime.last_error_code = self._safe_code(type(exc).__name__)
            runtime.lifecycle = "error"
            with suppress(RoomNotFoundError):
                runtime.recording_active = (
                    await self.recording_service.get_state(runtime.room_key)
                ).active
            base = 15
            with suppress(RoomNotFoundError):
                base = (await self.room_service.get_room(runtime.room_key)).poll_interval_seconds
            delay = self._error_delay(base, runtime.consecutive_errors)
            runtime.next_delay_seconds = delay
            runtime.next_check_at_ms = runtime.last_checked_at_ms + int(delay * 1000)

    def _normal_delay(self, base: int) -> float:
        return float(base) + self._jitter()

    def _error_delay(self, base: int, errors: int) -> float:
        multiplier = 2 ** min(max(errors, 1), 5)
        return min(float(base) * multiplier, _MAX_ERROR_BACKOFF_SECONDS) + self._jitter()

    def _jitter(self) -> float:
        if self.jitter_seconds <= 0:
            return 0.0
        return max(0.0, self.random_uniform(0.0, float(self.jitter_seconds)))

    @staticmethod
    def _safe_code(value: str) -> str:
        normalized = _ERROR_CODE_RE.sub("_", value.casefold()).strip("_")
        return (normalized or "unknown")[:80]

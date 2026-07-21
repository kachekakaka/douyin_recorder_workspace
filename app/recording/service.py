from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol

from app.media import (
    ProgressSnapshot,
    RecorderResult,
    RecorderSupervisor,
    RecordingPlan,
    StreamInput,
    parse_segment_csv,
)
from app.recording.models import RecordingState
from app.recording.repository import (
    RecordingAlreadyActiveError,
    RecordingSessionError,
    RecordingSessionRepository,
)
from app.rooms import RoomService
from app.sessions import RecipientSessionService, RecipientSessionStateError
from app.settings import Settings


class RecorderLike(Protocol):
    running: bool

    async def start(self) -> None: ...

    async def wait(self) -> RecorderResult: ...

    async def stop(
        self,
        *,
        graceful_timeout: float = 10.0,
        terminate_timeout: float = 5.0,
    ) -> RecorderResult: ...


SupervisorFactory = Callable[..., RecorderLike]


class RecordingCandidateUnavailableError(RecordingSessionError):
    """Raised when no safe in-memory candidate is available for the room."""


class RecordingRoomDisabledError(RecordingSessionError):
    """Raised when an explicit recording start targets a disabled room."""


@dataclass(slots=True)
class _ActiveRecording:
    room_key: str
    session_id: str
    plan: RecordingPlan
    supervisor: RecorderLike
    wait_task: asyncio.Task[None] | None = None
    stop_reason: str | None = None
    finalized: bool = False
    finalize_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_segment_sync: float = 0.0


class SingleRoomRecordingService:
    def __init__(
        self,
        *,
        repository: RecordingSessionRepository,
        room_service: RoomService,
        recipient_service: RecipientSessionService,
        settings: Settings,
        runtime_instance_id: str,
        supervisor_factory: SupervisorFactory = RecorderSupervisor,
        session_id_factory: Callable[[], str] | None = None,
        wall_time_ms: Callable[[], int] | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        monotonic: Callable[[], float] = time.monotonic,
        segment_seconds: int = 600,
    ) -> None:
        if not 10 <= segment_seconds <= 86_400:
            raise ValueError("segment_seconds 必须在 10–86400 之间")
        self.repository = repository
        self.room_service = room_service
        self.recipient_service = recipient_service
        self.settings = settings
        self.runtime_instance_id = runtime_instance_id
        self.supervisor_factory = supervisor_factory
        self.session_id_factory = session_id_factory or (lambda: uuid.uuid4().hex)
        self.wall_time_ms = wall_time_ms or (lambda: int(time.time() * 1000))
        self.monotonic_ns = monotonic_ns
        self.monotonic = monotonic
        self.segment_seconds = segment_seconds
        self._active: dict[str, _ActiveRecording] = {}
        self._room_locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._closed = False

    async def recover(self) -> list[str]:
        return await self.repository.recover_interrupted(
            runtime_instance_id=self.runtime_instance_id,
            at_ms=self.wall_time_ms(),
        )

    async def get_state(self, room_key: str) -> RecordingState:
        await self.room_service.get_room(room_key)
        return await self.repository.get_state(room_key)

    async def list_sessions(
        self,
        *,
        room_key: str,
        limit: int = 100,
        offset: int = 0,
    ):
        await self.room_service.get_room(room_key)
        return await self.repository.list_sessions(
            room_key=room_key,
            limit=limit,
            offset=offset,
        )

    async def list_segments(
        self,
        *,
        room_key: str,
        session_id: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ):
        await self.room_service.get_room(room_key)
        return await self.repository.list_segments(
            room_key=room_key,
            session_id=session_id,
            limit=limit,
            offset=offset,
        )

    async def start_recording(self, room_key: str) -> RecordingState:
        if self._closed:
            raise RecordingSessionError("recording service 已关闭")
        lock = await self._room_lock(room_key)
        async with lock:
            existing = self._active.get(room_key)
            if existing is not None and not existing.finalized:
                raise RecordingAlreadyActiveError(room_key)
            room = await self.room_service.get_room(room_key)
            if not room.enabled:
                raise RecordingRoomDisabledError(room_key)
            candidate = self.room_service.stream_resolver.select_cached_candidate(
                room.room_url,
                protocol=room.protocol,
                quality=room.quality,
            )
            if candidate is None:
                result = await self.room_service.check_room(room_key)
                candidate = self.room_service.stream_resolver.select_cached_candidate(
                    room.room_url,
                    protocol=room.protocol,
                    quality=room.quality,
                )
                if candidate is None and result.candidates:
                    candidate = next(
                        (item for item in result.candidates if item.protocol == room.protocol),
                        None,
                    )
            if candidate is None:
                raise RecordingCandidateUnavailableError(room_key)

            session_id = self.session_id_factory()
            started_at_ms = self.wall_time_ms()
            started_monotonic_ns = self.monotonic_ns()
            public = candidate.to_public_dict()
            stream = StreamInput(
                url=candidate.url,
                protocol=candidate.protocol,
                quality=candidate.quality,
            )
            plan = RecordingPlan(
                ffmpeg_path=self.settings.ffmpeg_path,
                room_key=room.room_key,
                session_id=session_id,
                stream=stream,
                output_root=self.settings.paths.records_dir,
                segment_seconds=self.segment_seconds,
                container="mkv",
            )
            latest = room.latest_check or {}
            try:
                await self.recipient_service.start_session(
                    session_id=session_id,
                    room_key=room.room_key,
                    started_at_ms=started_at_ms,
                    started_monotonic_ns=started_monotonic_ns,
                    runtime_instance_id=self.runtime_instance_id,
                    external_room_id=(
                        str(latest.get("external_room_id"))
                        if latest.get("external_room_id") is not None
                        else None
                    ),
                    title=str(latest.get("title") or ""),
                    recording_protocol=candidate.protocol,
                    recording_quality=candidate.quality,
                    input_host=str(public["host"]),
                    input_path_sha256=str(public["path_sha256"]),
                    input_url_sha256=str(public["url_sha256"]),
                    input_query_keys_json=json.dumps(
                        public["query_keys"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    recording_container=plan.container,
                    segment_seconds=plan.segment_seconds,
                )
            except RecipientSessionStateError as exc:
                raise RecordingAlreadyActiveError(room_key) from exc

            async def on_progress(snapshot: ProgressSnapshot) -> None:
                await self.repository.update_progress(session_id, snapshot)
                active = self._active.get(room_key)
                if active is None or active.session_id != session_id:
                    return
                now = self.monotonic()
                if now - active.last_segment_sync >= 2.0:
                    active.last_segment_sync = now
                    await self._sync_segments(active)

            try:
                process_spec = plan.process_spec()
                supervisor = self.supervisor_factory(
                    process_spec,
                    on_progress=on_progress,
                )
            except Exception as exc:
                await self._fail_started_session(
                    session_id=session_id,
                    error_code=f"prepare_{type(exc).__name__.casefold()}",
                )
                raise RecordingSessionError("FFmpeg 录制计划准备失败") from exc

            active = _ActiveRecording(
                room_key=room_key,
                session_id=session_id,
                plan=plan,
                supervisor=supervisor,
            )
            self._active[room_key] = active
            try:
                await supervisor.start()
            except Exception as exc:
                self._active.pop(room_key, None)
                await self._fail_started_session(
                    session_id=session_id,
                    error_code=f"start_{type(exc).__name__.casefold()}",
                )
                raise RecordingSessionError("FFmpeg 录制进程启动失败") from exc
            active.wait_task = asyncio.create_task(
                self._wait_for_exit(active),
                name=f"recording-{room_key}-{session_id}",
            )
            return await self.repository.get_state(room_key)

    async def stop_recording(
        self,
        room_key: str,
        *,
        reason: str = "explicit_stop",
    ) -> RecordingState:
        await self.room_service.get_room(room_key)
        lock = await self._room_lock(room_key)
        async with lock:
            active = self._active.get(room_key)
            if active is None or active.finalized:
                return await self.repository.get_state(room_key)
            active.stop_reason = reason[:120]
        result = await active.supervisor.stop()
        await self._finalize(active, result)
        return await self.repository.get_state(room_key)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        rooms = list(self._active)
        if rooms:
            await asyncio.gather(
                *(self.stop_recording(room_key, reason="app_shutdown") for room_key in rooms),
                return_exceptions=True,
            )
        tasks = [
            active.wait_task for active in self._active.values() if active.wait_task is not None
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_for_exit(self, active: _ActiveRecording) -> None:
        try:
            result = await active.supervisor.wait()
        except Exception:
            await self._finalize(active, None, error_code="wait_failed")
            return
        await self._finalize(active, result)

    async def _finalize(
        self,
        active: _ActiveRecording,
        result: RecorderResult | None,
        *,
        error_code: str | None = None,
    ) -> None:
        async with active.finalize_lock:
            if active.finalized:
                return
            active.finalized = True
            await self._sync_segments(active)
            reason = active.stop_reason
            if reason == "app_shutdown":
                final_status = "interrupted"
                end_reason = "app_shutdown"
            elif reason:
                final_status = "ended"
                end_reason = reason
            elif result is not None and result.returncode == 0:
                final_status = "ended"
                end_reason = "natural_exit"
            else:
                final_status = "failed"
                end_reason = "ffmpeg_exit_nonzero" if result is not None else "wait_failed"
            await self._close_recipient_session(
                active,
                final_status=final_status,
                end_reason=end_reason,
            )
            await self.repository.record_result(
                session_id=active.session_id,
                result=result,
                error_code=error_code
                or (
                    None
                    if result is None or result.returncode == 0 or active.stop_reason
                    else "ffmpeg_exit_nonzero"
                ),
            )
            lock = await self._room_lock(active.room_key)
            async with lock:
                current = self._active.get(active.room_key)
                if current is active:
                    self._active.pop(active.room_key, None)

    async def _sync_segments(self, active: _ActiveRecording) -> None:
        try:
            session = await self.repository.get_session(active.session_id)
            entries = parse_segment_csv(active.plan.segment_list_path)
            await self.repository.sync_segments(
                session=session,
                plan=active.plan,
                entries=entries,
            )
        except (OSError, RecordingSessionError):
            return

    async def _fail_started_session(self, *, session_id: str, error_code: str) -> None:
        with suppress(RecipientSessionStateError):
            await self.recipient_service.end_session(
                session_id=session_id,
                at_ms=self.wall_time_ms(),
                monotonic_ns=self.monotonic_ns(),
                runtime_instance_id=self.runtime_instance_id,
                end_reason="recorder_start_failed",
                final_status="failed",
            )
        await self.repository.record_result(
            session_id=session_id,
            result=None,
            error_code=error_code,
        )

    async def _close_recipient_session(
        self,
        active: _ActiveRecording,
        *,
        final_status: str,
        end_reason: str,
    ) -> None:
        try:
            await self.recipient_service.end_session(
                session_id=active.session_id,
                at_ms=self.wall_time_ms(),
                monotonic_ns=self.monotonic_ns(),
                runtime_instance_id=self.runtime_instance_id,
                end_reason=end_reason,
                final_status=final_status,
            )
        except RecipientSessionStateError:
            return

    async def _room_lock(self, room_key: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._room_locks.setdefault(room_key, asyncio.Lock())

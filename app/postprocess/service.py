from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Protocol

from app.postprocess.executor import FFmpegPostprocessExecutor, OutputExecutionResult
from app.postprocess.models import ExportOutputPlan, PostprocessJobRecord
from app.postprocess.planner import ExportPlanner
from app.postprocess.repository import PostprocessRepository


class OutputExecutor(Protocol):
    async def run_output(
        self,
        *,
        job_id: str,
        attempt_id: str,
        output: ExportOutputPlan,
        cancel_event: asyncio.Event,
        allow_existing_final: bool = False,
    ) -> OutputExecutionResult: ...


class PostprocessService:
    def __init__(
        self,
        *,
        repository: PostprocessRepository,
        planner: ExportPlanner,
        executor: OutputExecutor,
        runtime_instance_id: str,
        enabled: bool,
        max_attempts: int = 3,
        poll_seconds: float = 1.0,
        wall_time_ms=lambda: int(time.time() * 1000),
    ) -> None:
        self.repository = repository
        self.planner = planner
        self.executor = executor
        self.runtime_instance_id = runtime_instance_id
        self.enabled = enabled
        self.max_attempts = max_attempts
        self.poll_seconds = poll_seconds
        self.wall_time_ms = wall_time_ms
        self._closed = False
        self._wake = asyncio.Event()
        self._worker_task: asyncio.Task[None] | None = None
        self._active_job_id: str | None = None
        self._active_cancel: asyncio.Event | None = None
        self._last_error_code: str | None = None

    @classmethod
    def create_default(
        cls,
        *,
        repository: PostprocessRepository,
        planner: ExportPlanner,
        ffmpeg_path: str,
        records_dir,
        userdata_dir,
        runtime_instance_id: str,
        enabled: bool,
        max_attempts: int,
    ) -> PostprocessService:
        return cls(
            repository=repository,
            planner=planner,
            executor=FFmpegPostprocessExecutor(
                ffmpeg_path=ffmpeg_path,
                records_dir=records_dir,
                userdata_dir=userdata_dir,
            ),
            runtime_instance_id=runtime_instance_id,
            enabled=enabled,
            max_attempts=max_attempts,
        )

    async def start(self) -> None:
        await self.repository.recover_interrupted(at_ms=self.wall_time_ms())
        if self.enabled and self._worker_task is None:
            self._worker_task = asyncio.create_task(
                self._worker_loop(), name="postprocess-worker"
            )

    async def close(self) -> None:
        self._closed = True
        self._wake.set()
        if self._active_cancel is not None:
            self._active_cancel.set()
        if self._worker_task is not None:
            with suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None

    async def create_export(self, session_id: str) -> PostprocessJobRecord:
        plan = await self.planner.build(session_id)
        job = await self.repository.create_job(
            plan,
            max_attempts=self.max_attempts,
            now_ms=self.wall_time_ms(),
        )
        self._wake.set()
        return job

    async def get_job(self, job_id: str) -> PostprocessJobRecord:
        return await self.repository.get_job(job_id)

    async def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PostprocessJobRecord]:
        return await self.repository.list_jobs(status=status, limit=limit, offset=offset)

    async def cancel_job(self, job_id: str) -> PostprocessJobRecord:
        job = await self.repository.request_cancel(job_id, at_ms=self.wall_time_ms())
        if self._active_job_id == job_id and self._active_cancel is not None:
            self._active_cancel.set()
        self._wake.set()
        return job

    async def retry_job(self, job_id: str) -> PostprocessJobRecord:
        job = await self.repository.retry(job_id, at_ms=self.wall_time_ms())
        self._wake.set()
        return job

    async def status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "running": bool(self._worker_task and not self._worker_task.done()),
            "active_job_id": self._active_job_id,
            "last_error_code": self._last_error_code,
        }

    async def _worker_loop(self) -> None:
        while not self._closed:
            try:
                claimed = await self.repository.claim_next(
                    runtime_instance_id=self.runtime_instance_id,
                    at_ms=self.wall_time_ms(),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._last_error_code = "claim_failed"
                self._wake.clear()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
                continue
            if claimed is None:
                self._wake.clear()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
                continue
            job, attempt_id = claimed
            self._last_error_code = None
            await self._run_job(job, attempt_id)

    async def _run_job(self, job: PostprocessJobRecord, attempt_id: str) -> None:
        self._active_job_id = job.id
        cancel_event = asyncio.Event()
        self._active_cancel = cancel_event
        returncode: int | None = None
        stop_stage: str | None = None
        try:
            plan = await self.repository.get_plan(job.id)
            output_records = {item.interval_id: item for item in job.outputs}
            for output in plan.outputs:
                if self._closed or await self.repository.is_cancel_requested(job.id):
                    cancel_event.set()
                record = output_records.get(output.interval_id)
                if record is not None and record.status == "succeeded":
                    continue
                await self.repository.set_output_status(
                    output_id=f"{job.id}:{output.interval_id}",
                    status="writing",
                    at_ms=self.wall_time_ms(),
                )
                result = await self.executor.run_output(
                    job_id=job.id,
                    attempt_id=attempt_id,
                    output=output,
                    cancel_event=cancel_event,
                    allow_existing_final=job.attempts > 1,
                )
                returncode = result.returncode
                stop_stage = result.stop_stage
                if result.canceled or cancel_event.is_set():
                    await self.repository.finish_job(
                        job_id=job.id,
                        attempt_id=attempt_id,
                        status="canceled",
                        at_ms=self.wall_time_ms(),
                        error_code="user_canceled" if not self._closed else "app_shutdown",
                        returncode=result.returncode,
                        stop_stage=result.stop_stage,
                    )
                    return
                if result.returncode != 0 or result.size_bytes is None or result.sha256 is None:
                    await self.repository.set_output_status(
                        output_id=f"{job.id}:{output.interval_id}",
                        status="failed",
                        at_ms=self.wall_time_ms(),
                    )
                    await self.repository.finish_job(
                        job_id=job.id,
                        attempt_id=attempt_id,
                        status="failed",
                        at_ms=self.wall_time_ms(),
                        error_code="ffmpeg_exit_nonzero",
                        returncode=result.returncode,
                        stop_stage=result.stop_stage,
                    )
                    return
                await self.repository.set_output_status(
                    output_id=f"{job.id}:{output.interval_id}",
                    status="succeeded",
                    at_ms=self.wall_time_ms(),
                    size_bytes=result.size_bytes,
                    sha256=result.sha256,
                )
            await self.repository.finish_job(
                job_id=job.id,
                attempt_id=attempt_id,
                status="succeeded",
                at_ms=self.wall_time_ms(),
                returncode=returncode or 0,
                stop_stage=stop_stage or "natural",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._last_error_code = "execution_failed"
            with suppress(Exception):
                await self.repository.finish_job(
                    job_id=job.id,
                    attempt_id=attempt_id,
                    status="canceled" if cancel_event.is_set() else "failed",
                    at_ms=self.wall_time_ms(),
                    error_code="user_canceled" if cancel_event.is_set() else "execution_failed",
                    returncode=returncode,
                    stop_stage=stop_stage,
                )
        finally:
            self._active_job_id = None
            self._active_cancel = None

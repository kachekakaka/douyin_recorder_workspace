from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.db import Database
from app.postprocess import (
    ExportOutputPlan,
    ExportPlan,
    ExportPlanError,
    ExportPlanner,
    OutputExecutionResult,
    PostprocessJobStateError,
    PostprocessRepository,
    PostprocessService,
)
from app.postprocess.executor import FFmpegPostprocessExecutor, PostprocessExecutionError

RECIPIENT_ID = "90071992547409931"


async def _seed_session(database: Database, *, status: str = "ended") -> None:
    await database.execute(
        "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, ?, ?)",
        ("runtime-a", "test", 1_000),
    )
    await database.execute(
        "INSERT INTO rooms(room_key, room_url, created_at_ms, updated_at_ms) "
        "VALUES (?, ?, ?, ?)",
        ("room-a", "https://live.douyin.com/79907888978", 1_000, 1_000),
    )
    await database.execute(
        """
        INSERT INTO sessions(
            id, room_key, status, started_at_ms, ended_at_ms, end_reason,
            runtime_instance_id, recording_protocol, recording_quality,
            recording_container, segment_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'flv', 'origin', 'mkv', 10)
        """,
        (
            "session-a",
            "room-a",
            status,
            1_000,
            4_000 if status != "active" else None,
            "test" if status != "active" else None,
            "runtime-a",
        ),
    )
    for sequence, start, end in ((0, 0.0, 1.0), (1, 1.0, 2.0), (2, 2.0, 3.0)):
        await database.execute(
            """
            INSERT INTO media_files(
                id, session_id, relative_path, sequence, requested_start_at_ms,
                actual_start_at_ms, actual_end_at_ms, size_bytes, status,
                segment_start_seconds, segment_end_seconds, container, media_suffix
            ) VALUES (?, 'session-a', ?, ?, ?, ?, ?, 100, 'closed', ?, ?, 'mkv', '.mkv')
            """,
            (
                f"session-a:{sequence:05d}",
                f"room-a/session-a/media/{sequence:05d}.mkv",
                sequence,
                1_000 + sequence * 1_000,
                1_000 + sequence * 1_000,
                2_000 + sequence * 1_000,
                start,
                end,
            ),
        )
    intervals = (
        (1, "waiting", "waiting_first_event", None, 1_000, 1_200),
        (2, "active", None, f"uid:{RECIPIENT_ID}", 1_200, 2_500),
        (3, "unknown", "im_disconnected", None, 2_500, 4_000),
    )
    for interval_id, state, reason, key, start_ms, end_ms in intervals:
        await database.execute(
            """
            INSERT INTO recipient_intervals(
                id, session_id, status, reason, recipient_key,
                started_at_ms, ended_at_ms, started_monotonic_ns,
                ended_monotonic_ns, runtime_instance_id,
                ended_runtime_instance_id
            ) VALUES (?, 'session-a', ?, ?, ?, ?, ?, ?, ?, 'runtime-a', 'runtime-a')
            """,
            (
                interval_id,
                state,
                reason,
                key,
                start_ms,
                end_ms,
                start_ms * 1_000,
                end_ms * 1_000,
            ),
        )


def test_export_planner_is_deterministic_and_hides_recipient_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "db.sqlite")
        await database.initialize()
        try:
            await _seed_session(database)
            planner = ExportPlanner(database)
            first = await planner.build("session-a")
            second = await planner.build("session-a")
            assert first == second
            assert len(first.outputs) == 3
            assert [item.interval_status for item in first.outputs] == [
                "waiting",
                "active",
                "unknown",
            ]
            active = first.outputs[1]
            assert active.duration_seconds == 1.3
            assert active.trim_start_seconds == 0.2
            assert len(active.sources) == 2
            rendered = json.dumps(first.to_dict(), sort_keys=True)
            assert RECIPIENT_ID not in rendered
            assert "uid:" not in rendered
            assert active.recipient_key_sha256
            assert active.recipient_key_sha256[:12] in active.relative_path
            assert first.idempotency_key == second.idempotency_key
        finally:
            await database.close()

    asyncio.run(scenario())


def test_export_planner_rejects_active_or_media_less_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "db.sqlite")
        await database.initialize()
        try:
            await _seed_session(database, status="active")
            with pytest.raises(ExportPlanError, match="已正常结束"):
                await ExportPlanner(database).build("session-a")
            await database.execute(
                "UPDATE sessions SET status='ended', ended_at_ms=4000 WHERE id=?",
                ("session-a",),
            )
            await database.execute("DELETE FROM media_files WHERE session_id=?", ("session-a",))
            with pytest.raises(ExportPlanError, match="媒体分片"):
                await ExportPlanner(database).build("session-a")
        finally:
            await database.close()

    asyncio.run(scenario())


def test_postprocess_repository_idempotency_retry_cancel_and_recovery(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "db.sqlite")
        await database.initialize()
        try:
            await _seed_session(database)
            planner = ExportPlanner(database)
            repository = PostprocessRepository(database)
            plan = await planner.build("session-a")
            first = await repository.create_job(plan, max_attempts=3, now_ms=10)
            duplicate = await repository.create_job(plan, max_attempts=3, now_ms=11)
            assert duplicate.id == first.id
            claimed = await repository.claim_next(runtime_instance_id="runtime-a", at_ms=20)
            assert claimed is not None
            running, attempt_id = claimed
            assert running.status == "running"
            assert running.attempts == 1
            await repository.finish_job(
                job_id=running.id,
                attempt_id=attempt_id,
                status="failed",
                at_ms=30,
                error_code="synthetic_failure",
                returncode=1,
            )
            retried = await repository.retry(running.id, at_ms=40)
            assert retried.status == "queued"
            canceled = await repository.request_cancel(running.id, at_ms=50)
            assert canceled.status == "canceled"
            with pytest.raises(PostprocessJobStateError, match="终态"):
                await repository.request_cancel(running.id, at_ms=60)

            other_outputs = tuple(
                replace(
                    item,
                    relative_path=item.relative_path.replace(
                        "exports/", "exports/other/", 1
                    ),
                )
                for item in plan.outputs
            )
            other = ExportPlan(
                session_id=plan.session_id,
                room_key=plan.room_key,
                idempotency_key="f" * 64,
                outputs=other_outputs,
            )
            queued = await repository.create_job(other, max_attempts=3, now_ms=70)
            claimed2 = await repository.claim_next(runtime_instance_id="runtime-a", at_ms=80)
            assert claimed2 is not None and claimed2[0].id == queued.id
            recovered = await repository.recover_interrupted(at_ms=90)
            assert recovered == [queued.id]
            assert (await repository.get_job(queued.id)).error_code == "app_restart_recovery"
        finally:
            await database.close()

    asyncio.run(scenario())


class _FakeExecutor:
    def __init__(self, *, block: bool = False) -> None:
        self.block = block
        self.started = asyncio.Event()

    async def run_output(
        self, *, job_id, attempt_id, output, cancel_event, allow_existing_final=False
    ):
        del job_id, attempt_id, output, allow_existing_final
        self.started.set()
        if self.block:
            await cancel_event.wait()
            return OutputExecutionResult(255, "graceful", True)
        return OutputExecutionResult(0, "natural", False, 123, "a" * 64)


async def _wait_for_status(repository: PostprocessRepository, job_id: str, status: str) -> None:
    for _ in range(200):
        if (await repository.get_job(job_id)).status == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"job did not reach {status}")


def test_postprocess_service_completes_and_running_cancel_is_canceled(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "db.sqlite")
        await database.initialize()
        try:
            await _seed_session(database)
            repository = PostprocessRepository(database)
            planner = ExportPlanner(database)
            executor = _FakeExecutor()
            service = PostprocessService(
                repository=repository,
                planner=planner,
                executor=executor,
                runtime_instance_id="runtime-a",
                enabled=True,
                poll_seconds=0.01,
            )
            await service.start()
            job = await service.create_export("session-a")
            await _wait_for_status(repository, job.id, "succeeded")
            completed = await repository.get_job(job.id)
            assert all(item.status == "succeeded" for item in completed.outputs)
            await service.close()

            await database.execute("DELETE FROM postprocess_outputs")
            await database.execute("DELETE FROM postprocess_attempts")
            await database.execute("DELETE FROM postprocess_jobs")
            blocking = _FakeExecutor(block=True)
            service2 = PostprocessService(
                repository=repository,
                planner=planner,
                executor=blocking,
                runtime_instance_id="runtime-a",
                enabled=True,
                poll_seconds=0.01,
            )
            await service2.start()
            job2 = await service2.create_export("session-a")
            await asyncio.wait_for(blocking.started.wait(), timeout=1)
            await service2.cancel_job(job2.id)
            await _wait_for_status(repository, job2.id, "canceled")
            assert (await repository.get_job(job2.id)).status == "canceled"
            await service2.close()
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ffmpeg_postprocess_path_boundaries(tmp_path: Path) -> None:
    records = tmp_path / "records"
    userdata = tmp_path / "userdata"
    records.mkdir()
    userdata.mkdir()
    media = records / "room" / "session" / "media"
    media.mkdir(parents=True)
    source = media / "00000.mkv"
    source.write_bytes(b"not-media")
    assert FFmpegPostprocessExecutor._resolve_source(
        records.resolve(), "room/session/media/00000.mkv"
    ) == source.resolve()
    with pytest.raises(PostprocessExecutionError, match="无效"):
        FFmpegPostprocessExecutor._resolve_output(records.resolve(), "../escape.mkv")
    final = records / "exports" / "job.mkv"
    final.parent.mkdir()
    link = final.parent / "link"
    try:
        link.symlink_to(media, target_is_directory=True)
    except (OSError, NotImplementedError):
        return
    with pytest.raises(PostprocessExecutionError, match="符号链接"):
        FFmpegPostprocessExecutor._resolve_source(
            records.resolve(), "exports/link/00000.mkv"
        )


def test_ffmpeg_postprocess_can_adopt_atomic_existing_output_on_retry(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        records = tmp_path / "records"
        userdata = tmp_path / "userdata"
        records.mkdir()
        userdata.mkdir()
        target = records / "exports" / "room" / "session" / "existing.mkv"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"atomic-complete-output")
        output = ExportOutputPlan(
            interval_id=1,
            interval_status="waiting",
            interval_reason="test",
            recipient_key_sha256="",
            sources=(),
            relative_path="exports/room/session/existing.mkv",
            trim_start_seconds=0.0,
            duration_seconds=1.0,
        )
        executor = FFmpegPostprocessExecutor(
            ffmpeg_path="ffmpeg",
            records_dir=records,
            userdata_dir=userdata,
        )
        with pytest.raises(PostprocessExecutionError, match="已存在"):
            await executor.run_output(
                job_id="job-a",
                attempt_id="attempt-first",
                output=output,
                cancel_event=asyncio.Event(),
            )
        recovered = await executor.run_output(
            job_id="job-a",
            attempt_id="attempt-retry",
            output=output,
            cancel_event=asyncio.Event(),
            allow_existing_final=True,
        )
        assert recovered.stop_stage == "recovered-existing"
        assert recovered.size_bytes == len(b"atomic-complete-output")
        assert recovered.sha256

    asyncio.run(scenario())

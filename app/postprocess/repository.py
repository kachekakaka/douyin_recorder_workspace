from __future__ import annotations

import json
import time
import uuid
from typing import Any

import aiosqlite

from app.db import Database
from app.postprocess.models import (
    ExportPlan,
    PostprocessJobRecord,
    PostprocessOutputRecord,
)


class PostprocessJobError(RuntimeError):
    """Base postprocess persistence error."""


class PostprocessJobNotFoundError(PostprocessJobError):
    """Raised when a job does not exist."""


class PostprocessJobStateError(PostprocessJobError):
    """Raised when an action is invalid for the current job state."""


class PostprocessRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_job(
        self,
        plan: ExportPlan,
        *,
        max_attempts: int,
        now_ms: int | None = None,
    ) -> PostprocessJobRecord:
        at_ms = int(time.time() * 1000) if now_ms is None else now_ms
        job_id = uuid.uuid4().hex
        plan_json = json.dumps(
            plan.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

        async def operation(connection: aiosqlite.Connection) -> str:
            cursor = await connection.execute(
                "SELECT id FROM postprocess_jobs WHERE idempotency_key = ?",
                (plan.idempotency_key,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is not None:
                return str(row["id"])
            await connection.execute(
                """
                INSERT INTO postprocess_jobs(
                    id, job_type, session_id, status, priority, attempts, max_attempts,
                    idempotency_key, plan_json, cancel_requested,
                    created_at_ms, updated_at_ms
                ) VALUES (?, 'recipient_export', ?, 'queued', 100, 0, ?, ?, ?, 0, ?, ?)
                """,
                (
                    job_id,
                    plan.session_id,
                    max_attempts,
                    plan.idempotency_key,
                    plan_json,
                    at_ms,
                    at_ms,
                ),
            )
            for output in plan.outputs:
                await connection.execute(
                    """
                    INSERT INTO postprocess_outputs(
                        id, job_id, interval_id, interval_status, interval_reason,
                        recipient_key_sha256, source_media_ids_json, relative_path,
                        trim_start_seconds, duration_seconds, status,
                        created_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        f"{job_id}:{output.interval_id}",
                        job_id,
                        output.interval_id,
                        output.interval_status,
                        output.interval_reason,
                        output.recipient_key_sha256,
                        json.dumps(
                            [item.media_id for item in output.sources],
                            separators=(",", ":"),
                        ),
                        output.relative_path,
                        output.trim_start_seconds,
                        output.duration_seconds,
                        at_ms,
                        at_ms,
                    ),
                )
            return job_id

        selected_id = await self.database.write(operation)
        return await self.get_job(selected_id)

    async def get_plan(self, job_id: str) -> ExportPlan:
        row = await self.database.fetch_one(
            "SELECT plan_json FROM postprocess_jobs WHERE id = ?", (job_id,)
        )
        if row is None:
            raise PostprocessJobNotFoundError(job_id)
        try:
            value = json.loads(str(row["plan_json"]))
        except json.JSONDecodeError as exc:
            raise PostprocessJobError("postprocess plan JSON 损坏") from exc
        from app.postprocess.models import ExportOutputPlan, ExportSource

        outputs = tuple(
            ExportOutputPlan(
                interval_id=int(item["interval_id"]),
                interval_status=str(item["interval_status"]),
                interval_reason=(
                    str(item["interval_reason"])
                    if item.get("interval_reason") is not None
                    else None
                ),
                recipient_key_sha256=str(item.get("recipient_key_sha256") or ""),
                sources=tuple(
                    ExportSource(
                        media_id=str(source["media_id"]),
                        relative_path=str(source["relative_path"]),
                        start_seconds=float(source["start_seconds"]),
                        end_seconds=float(source["end_seconds"]),
                    )
                    for source in item["sources"]
                ),
                relative_path=str(item["relative_path"]),
                trim_start_seconds=float(item["trim_start_seconds"]),
                duration_seconds=float(item["duration_seconds"]),
            )
            for item in value["outputs"]
        )
        return ExportPlan(
            session_id=str(value["session_id"]),
            room_key=str(value["room_key"]),
            idempotency_key=str(value["idempotency_key"]),
            outputs=outputs,
        )

    async def get_job(self, job_id: str) -> PostprocessJobRecord:
        row = await self.database.fetch_one(
            "SELECT * FROM postprocess_jobs WHERE id = ?", (job_id,)
        )
        if row is None:
            raise PostprocessJobNotFoundError(job_id)
        outputs = await self._list_outputs(job_id)
        return self._decode_job(row, outputs)

    async def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PostprocessJobRecord]:
        if not 1 <= limit <= 500 or not 0 <= offset <= 1_000_000:
            raise ValueError("分页参数超出允许范围")
        params: tuple[object, ...]
        if status is None:
            sql = (
                "SELECT * FROM postprocess_jobs "
                "ORDER BY created_at_ms DESC, id DESC LIMIT ? OFFSET ?"
            )
            params = (limit, offset)
        else:
            sql = (
                "SELECT * FROM postprocess_jobs WHERE status = ? "
                "ORDER BY created_at_ms DESC, id DESC LIMIT ? OFFSET ?"
            )
            params = (status, limit, offset)
        rows = await self.database.fetch_all(sql, params)
        result = []
        for row in rows:
            result.append(self._decode_job(row, await self._list_outputs(str(row["id"]))))
        return result

    async def claim_next(
        self,
        *,
        runtime_instance_id: str,
        at_ms: int,
    ) -> tuple[PostprocessJobRecord, str] | None:
        async def operation(
            connection: aiosqlite.Connection,
        ) -> tuple[str, str] | None:
            cursor = await connection.execute(
                """
                SELECT id, attempts FROM postprocess_jobs
                WHERE status = 'queued' AND cancel_requested = 0 AND attempts < max_attempts
                ORDER BY priority, created_at_ms, id LIMIT 1
                """
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                return None
            job_id = str(row["id"])
            attempt_number = int(row["attempts"]) + 1
            attempt_id = f"{job_id}:{attempt_number}"
            cursor = await connection.execute(
                """
                UPDATE postprocess_jobs
                SET status = 'running', attempts = ?, runtime_instance_id = ?,
                    started_at_ms = COALESCE(started_at_ms, ?), updated_at_ms = ?,
                    error_code = NULL
                WHERE id = ? AND status = 'queued' AND cancel_requested = 0
                """,
                (attempt_number, runtime_instance_id, at_ms, at_ms, job_id),
            )
            if cursor.rowcount != 1:
                return None
            await connection.execute(
                """
                INSERT INTO postprocess_attempts(
                    id, job_id, attempt_number, status, runtime_instance_id, started_at_ms
                ) VALUES (?, ?, ?, 'running', ?, ?)
                """,
                (attempt_id, job_id, attempt_number, runtime_instance_id, at_ms),
            )
            await connection.execute(
                "UPDATE postprocess_outputs SET status = 'queued', updated_at_ms = ? "
                "WHERE job_id = ? AND status IN ('failed', 'canceled')",
                (at_ms, job_id),
            )
            return job_id, attempt_id

        claimed = await self.database.write(operation)
        if claimed is None:
            return None
        return await self.get_job(claimed[0]), claimed[1]

    async def set_output_status(
        self,
        *,
        output_id: str,
        status: str,
        at_ms: int,
        size_bytes: int | None = None,
        sha256: str | None = None,
    ) -> None:
        await self.database.execute(
            """
            UPDATE postprocess_outputs
            SET status = ?, size_bytes = COALESCE(?, size_bytes),
                sha256 = COALESCE(?, sha256), updated_at_ms = ?
            WHERE id = ?
            """,
            (status, size_bytes, sha256, at_ms, output_id),
        )

    async def finish_job(
        self,
        *,
        job_id: str,
        attempt_id: str,
        status: str,
        at_ms: int,
        error_code: str | None = None,
        returncode: int | None = None,
        stop_stage: str | None = None,
    ) -> PostprocessJobRecord:
        if status not in {"succeeded", "failed", "canceled"}:
            raise ValueError("非法的 postprocess 结束状态")
        attempt_status = status

        async def operation(connection: aiosqlite.Connection) -> None:
            await connection.execute(
                """
                UPDATE postprocess_attempts
                SET status = ?, ended_at_ms = ?, returncode = ?, stop_stage = ?, error_code = ?
                WHERE id = ? AND status = 'running'
                """,
                (attempt_status, at_ms, returncode, stop_stage, error_code, attempt_id),
            )
            await connection.execute(
                """
                UPDATE postprocess_jobs
                SET status = ?, error_code = ?, ended_at_ms = ?, updated_at_ms = ?,
                    cancel_requested = CASE WHEN ? = 'canceled' THEN 1 ELSE cancel_requested END
                WHERE id = ?
                """,
                (status, error_code, at_ms, at_ms, status, job_id),
            )
            if status in {"failed", "canceled"}:
                await connection.execute(
                    "UPDATE postprocess_outputs SET status = ?, updated_at_ms = ? "
                    "WHERE job_id = ? AND status IN ('queued', 'writing')",
                    (status, at_ms, job_id),
                )

        await self.database.write(operation)
        return await self.get_job(job_id)

    async def request_cancel(self, job_id: str, *, at_ms: int) -> PostprocessJobRecord:
        async def operation(connection: aiosqlite.Connection) -> None:
            cursor = await connection.execute(
                "SELECT status FROM postprocess_jobs WHERE id = ?", (job_id,)
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise PostprocessJobNotFoundError(job_id)
            status = str(row["status"])
            if status in {"succeeded", "failed", "canceled"}:
                raise PostprocessJobStateError("终态任务不能取消")
            if status == "queued":
                await connection.execute(
                    "UPDATE postprocess_jobs SET status = 'canceled', cancel_requested = 1, "
                    "ended_at_ms = ?, updated_at_ms = ? WHERE id = ?",
                    (at_ms, at_ms, job_id),
                )
                await connection.execute(
                    "UPDATE postprocess_outputs SET status = 'canceled', updated_at_ms = ? "
                    "WHERE job_id = ? AND status = 'queued'",
                    (at_ms, job_id),
                )
            else:
                await connection.execute(
                    "UPDATE postprocess_jobs SET cancel_requested = 1, updated_at_ms = ? "
                    "WHERE id = ?",
                    (at_ms, job_id),
                )

        await self.database.write(operation)
        return await self.get_job(job_id)

    async def retry(self, job_id: str, *, at_ms: int) -> PostprocessJobRecord:
        async def operation(connection: aiosqlite.Connection) -> None:
            cursor = await connection.execute(
                "SELECT status, attempts, max_attempts FROM postprocess_jobs WHERE id = ?",
                (job_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise PostprocessJobNotFoundError(job_id)
            if str(row["status"]) not in {"failed", "canceled"}:
                raise PostprocessJobStateError("仅失败或取消的任务可以重试")
            if int(row["attempts"]) >= int(row["max_attempts"]):
                raise PostprocessJobStateError("任务已达到最大尝试次数")
            await connection.execute(
                """
                UPDATE postprocess_jobs
                SET status = 'queued', cancel_requested = 0, error_code = NULL,
                    started_at_ms = NULL, ended_at_ms = NULL, updated_at_ms = ?
                WHERE id = ?
                """,
                (at_ms, job_id),
            )
            await connection.execute(
                "UPDATE postprocess_outputs SET status = 'queued', updated_at_ms = ? "
                "WHERE job_id = ? AND status IN ('failed', 'canceled')",
                (at_ms, job_id),
            )

        await self.database.write(operation)
        return await self.get_job(job_id)

    async def is_cancel_requested(self, job_id: str) -> bool:
        row = await self.database.fetch_one(
            "SELECT cancel_requested FROM postprocess_jobs WHERE id = ?", (job_id,)
        )
        return bool(row and int(row["cancel_requested"]))

    async def recover_interrupted(self, *, at_ms: int) -> list[str]:
        async def operation(connection: aiosqlite.Connection) -> list[str]:
            cursor = await connection.execute(
                "SELECT id FROM postprocess_jobs WHERE status = 'running'"
            )
            rows = await cursor.fetchall()
            await cursor.close()
            job_ids = [str(row["id"]) for row in rows]
            for job_id in job_ids:
                await connection.execute(
                    "UPDATE postprocess_attempts SET status = 'interrupted', ended_at_ms = ?, "
                    "error_code = 'app_restart_recovery' "
                    "WHERE job_id = ? AND status = 'running'",
                    (at_ms, job_id),
                )
                await connection.execute(
                    "UPDATE postprocess_jobs SET status = 'failed', "
                    "error_code = 'app_restart_recovery', ended_at_ms = ?, "
                    "updated_at_ms = ? WHERE id = ?",
                    (at_ms, at_ms, job_id),
                )
                await connection.execute(
                    "UPDATE postprocess_outputs SET status = 'failed', updated_at_ms = ? "
                    "WHERE job_id = ? AND status IN ('queued', 'writing')",
                    (at_ms, job_id),
                )
            return job_ids

        return await self.database.write(operation)

    async def _list_outputs(self, job_id: str) -> tuple[PostprocessOutputRecord, ...]:
        rows = await self.database.fetch_all(
            "SELECT * FROM postprocess_outputs WHERE job_id = ? ORDER BY interval_id, id",
            (job_id,),
        )
        return tuple(self._decode_output(row) for row in rows)

    @staticmethod
    def _decode_job(
        row: dict[str, Any], outputs: tuple[PostprocessOutputRecord, ...]
    ) -> PostprocessJobRecord:
        return PostprocessJobRecord(
            id=str(row["id"]),
            job_type=str(row["job_type"]),
            session_id=str(row["session_id"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            priority=int(row["priority"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            idempotency_key=str(row["idempotency_key"]),
            cancel_requested=bool(int(row["cancel_requested"])),
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
            started_at_ms=(
                int(row["started_at_ms"]) if row["started_at_ms"] is not None else None
            ),
            ended_at_ms=int(row["ended_at_ms"]) if row["ended_at_ms"] is not None else None,
            outputs=outputs,
        )

    @staticmethod
    def _decode_output(row: dict[str, Any]) -> PostprocessOutputRecord:
        try:
            ids = json.loads(str(row["source_media_ids_json"]))
        except json.JSONDecodeError:
            ids = []
        return PostprocessOutputRecord(
            id=str(row["id"]),
            job_id=str(row["job_id"]),
            interval_id=int(row["interval_id"]),
            interval_status=str(row["interval_status"]),
            interval_reason=(
                str(row["interval_reason"]) if row["interval_reason"] is not None else None
            ),
            recipient_key_sha256=str(row["recipient_key_sha256"] or ""),
            source_media_ids=tuple(str(item) for item in ids if isinstance(item, str)),
            relative_path=str(row["relative_path"]),
            trim_start_seconds=float(row["trim_start_seconds"]),
            duration_seconds=float(row["duration_seconds"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            size_bytes=int(row["size_bytes"]) if row["size_bytes"] is not None else None,
            sha256=str(row["sha256"]) if row["sha256"] is not None else None,
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
        )

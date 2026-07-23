from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.core import Database  # noqa: E402
from app.postprocess.repository import PostprocessRepository  # noqa: E402
from app.recording.repository import RecordingSessionRepository  # noqa: E402

_ROOM_KEY = "stability-recovery-room"
_ROOM_URL = "https://live.douyin.com/73504089679"


async def _seed_cycle(database: Database, cycle: int) -> tuple[str, str, int, str]:
    old_runtime = f"runtime-old-{cycle:03d}"
    new_runtime = f"runtime-new-{cycle:03d}"
    session_id = f"session-{cycle:03d}"
    job_id = f"job-{cycle:03d}"
    interval_start = cycle * 10_000 + 100
    await database.execute(
        "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, 'test', ?)",
        (old_runtime, interval_start - 10),
    )
    await database.execute(
        "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, 'test', ?)",
        (new_runtime, interval_start + 10),
    )
    await database.execute(
        "INSERT INTO sessions("
        "id, room_key, status, started_at_ms, runtime_instance_id, "
        "started_monotonic_ns, recording_protocol, recording_quality, segment_seconds"
        ") VALUES (?, ?, 'active', ?, ?, ?, 'flv', 'origin', 600)",
        (session_id, _ROOM_KEY, interval_start, old_runtime, interval_start * 1_000_000),
    )
    await database.execute(
        "INSERT INTO recipient_intervals("
        "session_id, status, reason, started_at_ms, started_monotonic_ns, "
        "runtime_instance_id"
        ") VALUES (?, 'waiting', 'waiting_first_event', ?, ?, ?)",
        (session_id, interval_start, interval_start * 1_000_000, old_runtime),
    )
    interval = await database.fetch_one(
        "SELECT id FROM recipient_intervals WHERE session_id = ?", (session_id,)
    )
    if interval is None:
        raise RuntimeError("failed to seed recipient interval")
    interval_id = int(interval["id"])
    plan = {
        "session_id": session_id,
        "room_key": _ROOM_KEY,
        "idempotency_key": f"stability-{cycle:03d}",
        "outputs": [],
    }
    await database.execute(
        "INSERT INTO postprocess_jobs("
        "id, job_type, session_id, status, priority, attempts, max_attempts, "
        "idempotency_key, plan_json, cancel_requested, runtime_instance_id, "
        "created_at_ms, updated_at_ms, started_at_ms"
        ") VALUES (?, 'recipient_export', ?, 'running', 100, 1, 3, ?, ?, 0, ?, ?, ?, ?)",
        (
            job_id,
            session_id,
            f"stability-{cycle:03d}",
            json.dumps(plan, separators=(",", ":")),
            old_runtime,
            interval_start,
            interval_start,
            interval_start,
        ),
    )
    attempt_id = f"{job_id}:1"
    await database.execute(
        "INSERT INTO postprocess_attempts("
        "id, job_id, attempt_number, status, runtime_instance_id, started_at_ms"
        ") VALUES (?, ?, 1, 'running', ?, ?)",
        (attempt_id, job_id, old_runtime, interval_start),
    )
    await database.execute(
        "INSERT INTO postprocess_outputs("
        "id, job_id, interval_id, interval_status, interval_reason, "
        "recipient_key_sha256, source_media_ids_json, relative_path, "
        "trim_start_seconds, duration_seconds, status, created_at_ms, updated_at_ms"
        ") VALUES (?, ?, ?, 'waiting', 'waiting_first_event', '', '[]', ?, 0, 1, "
        "'writing', ?, ?)",
        (
            f"{job_id}:{interval_id}",
            job_id,
            interval_id,
            f"exports/stability/{cycle:03d}.mkv",
            interval_start,
            interval_start,
        ),
    )
    return session_id, job_id, interval_id, new_runtime


async def _run_cycles(database_path: Path, cycles: int) -> dict[str, Any]:
    database = Database(database_path)
    await database.initialize()
    recording = RecordingSessionRepository(database)
    postprocess = PostprocessRepository(database)
    await database.execute(
        "INSERT INTO rooms(room_key, room_url, enabled, created_at_ms, updated_at_ms) "
        "VALUES (?, ?, 1, 1, 1)",
        (_ROOM_KEY, _ROOM_URL),
    )
    recovered_sessions = 0
    recovered_jobs = 0
    idempotent_passes = 0
    try:
        for cycle in range(1, cycles + 1):
            session_id, job_id, interval_id, new_runtime = await _seed_cycle(database, cycle)
            at_ms = cycle * 10_000 + 500
            sessions = await recording.recover_interrupted(
                runtime_instance_id=new_runtime, at_ms=at_ms
            )
            jobs = await postprocess.recover_interrupted(at_ms=at_ms)
            if sessions != [session_id] or jobs != [job_id]:
                raise RuntimeError("recovery selected an unexpected record set")
            recovered_sessions += len(sessions)
            recovered_jobs += len(jobs)

            session = await database.fetch_one(
                "SELECT status, ended_at_ms, end_reason, ended_runtime_instance_id, "
                "recording_error_code FROM sessions WHERE id = ?",
                (session_id,),
            )
            interval = await database.fetch_one(
                "SELECT ended_at_ms, ended_runtime_instance_id FROM recipient_intervals "
                "WHERE id = ?",
                (interval_id,),
            )
            job = await database.fetch_one(
                "SELECT status, ended_at_ms, error_code FROM postprocess_jobs WHERE id = ?",
                (job_id,),
            )
            attempt = await database.fetch_one(
                "SELECT status, ended_at_ms, error_code FROM postprocess_attempts "
                "WHERE job_id = ?",
                (job_id,),
            )
            output = await database.fetch_one(
                "SELECT status FROM postprocess_outputs WHERE job_id = ?", (job_id,)
            )
            if session != {
                "status": "interrupted",
                "ended_at_ms": at_ms,
                "end_reason": "app_restart_recovery",
                "ended_runtime_instance_id": new_runtime,
                "recording_error_code": "app_restart_recovery",
            }:
                raise RuntimeError("recording recovery state mismatch")
            if interval != {
                "ended_at_ms": at_ms,
                "ended_runtime_instance_id": new_runtime,
            }:
                raise RuntimeError("recipient interval recovery state mismatch")
            if job != {
                "status": "failed",
                "ended_at_ms": at_ms,
                "error_code": "app_restart_recovery",
            }:
                raise RuntimeError("postprocess job recovery state mismatch")
            if attempt != {
                "status": "interrupted",
                "ended_at_ms": at_ms,
                "error_code": "app_restart_recovery",
            }:
                raise RuntimeError("postprocess attempt recovery state mismatch")
            if output != {"status": "failed"}:
                raise RuntimeError("postprocess output recovery state mismatch")

            repeated_sessions = await recording.recover_interrupted(
                runtime_instance_id=new_runtime, at_ms=at_ms + 1
            )
            repeated_jobs = await postprocess.recover_interrupted(at_ms=at_ms + 1)
            if repeated_sessions or repeated_jobs:
                raise RuntimeError("recovery is not idempotent")
            idempotent_passes += 1
    finally:
        await database.close()
    return {
        "smoke_version": 1,
        "cycles": cycles,
        "recovered_recording_sessions": recovered_sessions,
        "recovered_postprocess_jobs": recovered_jobs,
        "idempotent_recovery_passes": idempotent_passes,
        "schema_version": 6,
        "uses_network": False,
        "live_verified": False,
        "passed": recovered_sessions == cycles
        and recovered_jobs == cycles
        and idempotent_passes == cycles,
    }


def run_startup_recovery_smoke(*, cycles: int = 25) -> dict[str, Any]:
    if not 1 <= cycles <= 100:
        raise ValueError("cycles must be between 1 and 100")
    with tempfile.TemporaryDirectory(prefix="douyin-startup-recovery-") as temp:
        return asyncio.run(_run_cycles(Path(temp) / "recovery.sqlite", cycles))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repeated synthetic recording and postprocess startup recovery smoke."
    )
    parser.add_argument("--cycles", type=int, default=25)
    parser.add_argument("--json-output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run_startup_recovery_smoke(cycles=args.cycles)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[失败] 启动恢复演练失败: {type(exc).__name__}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_output is None:
        print(rendered, end="")
    else:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered, encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

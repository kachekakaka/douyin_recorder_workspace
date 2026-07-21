from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import __version__  # noqa: E402
from app.db import Database  # noqa: E402
from app.douyin.recipient import RecipientContract  # noqa: E402
from app.media import (  # noqa: E402
    RecorderProcessSpec,
    RecorderSupervisor,
    RecordingPlan,
    StreamInput,
    parse_segment_csv,
)
from app.recording import RecordingSessionRepository  # noqa: E402
from app.rooms import RoomCreate, RoomRepository  # noqa: E402
from app.sessions import RecipientSessionRepository, RecipientSessionService  # noqa: E402
from app.settings import Settings  # noqa: E402


async def run_smoke(output_dir: Path, *, duration_seconds: float = 2.0) -> dict[str, object]:
    if not 1 <= duration_seconds <= 30:
        raise ValueError("duration_seconds 必须在 1–30 之间")
    settings = Settings.load()
    ffmpeg = shutil.which(settings.ffmpeg_path) or (
        settings.ffmpeg_path if Path(settings.ffmpeg_path).is_file() else None
    )
    if ffmpeg is None:
        raise RuntimeError("未找到 FFmpeg")

    output_dir = output_dir.expanduser().absolute()
    if output_dir.exists() or output_dir.is_symlink():
        raise RuntimeError(f"输出目录已存在或为符号链接，拒绝覆盖: {output_dir}")
    output_dir.mkdir(parents=True)

    database = Database(output_dir / "userdata" / "smoke.db")
    runtime_id = "recording-smoke-runtime"
    room_key = "recording-smoke"
    session_id = "recording-smoke-session"
    contract = RecipientContract.load(ROOT / "app" / "douyin" / "contracts" / "provisional_v1.json")
    rooms = RoomRepository(database)
    recipient_repository = RecipientSessionRepository(database)
    recipient_service = RecipientSessionService(recipient_repository, contract)
    recording_repository = RecordingSessionRepository(database)

    await database.initialize()
    try:
        await database.execute(
            "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, ?, ?)",
            (runtime_id, __version__, 1_000),
        )
        await rooms.create_room(
            RoomCreate(
                room_key=room_key,
                room_url="73504089679",
                quality="origin",
                protocol="flv",
            )
        )
        plan = RecordingPlan(
            ffmpeg_path=ffmpeg,
            room_key=room_key,
            session_id=session_id,
            stream=StreamInput(
                url="https://pull.example.douyincdn.com/live/smoke.flv",
                protocol="flv",
                quality="origin",
            ),
            output_root=output_dir / "records",
            segment_seconds=10,
            container="mkv",
        )
        await recipient_service.start_session(
            session_id=session_id,
            room_key=room_key,
            started_at_ms=1_000,
            started_monotonic_ns=1_000_000,
            runtime_instance_id=runtime_id,
            title="synthetic recording session smoke",
            recording_protocol="flv",
            recording_quality="origin",
            input_host="pull.example.douyincdn.com",
            input_path_sha256="a" * 64,
            input_url_sha256="b" * 64,
            input_query_keys_json="[]",
            recording_container="mkv",
            segment_seconds=10,
        )

        plan.media_dir.mkdir(parents=True)
        argv = (
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-n",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000",
            "-t",
            str(duration_seconds),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-c:a",
            "pcm_s16le",
            "-f",
            "segment",
            "-segment_time",
            "1",
            "-reset_timestamps",
            "1",
            "-segment_list",
            str(plan.segment_list_path),
            "-segment_list_type",
            "csv",
            "-progress",
            "pipe:1",
            str(plan.output_pattern),
        )
        spec = RecorderProcessSpec(
            argv=argv,
            redacted_argv=argv,
            stderr_log_path=plan.stderr_log_path,
            cwd=plan.session_dir,
        )

        async def on_progress(snapshot) -> None:
            await recording_repository.update_progress(session_id, snapshot)

        supervisor = RecorderSupervisor(spec, on_progress=on_progress)
        await supervisor.start()
        result = await supervisor.wait()
        session = await recording_repository.get_session(session_id)
        entries = parse_segment_csv(plan.segment_list_path)
        synced = await recording_repository.sync_segments(
            session=session,
            plan=plan,
            entries=entries,
        )
        await recipient_service.end_session(
            session_id=session_id,
            at_ms=2_000,
            monotonic_ns=2_000_000,
            runtime_instance_id=runtime_id,
            end_reason="smoke_natural_exit",
            final_status="ended" if result.returncode == 0 else "failed",
        )
        await recording_repository.record_result(
            session_id=session_id,
            result=result,
            error_code=None if result.returncode == 0 else "ffmpeg_exit_nonzero",
        )

        final_session = await recording_repository.get_session(session_id)
        segments = await recording_repository.list_segments(
            room_key=room_key,
            session_id=session_id,
        )
        recipient = await recipient_repository.get_state(room_key)
        media = [
            {
                "relative_path": item.relative_path,
                "size_bytes": item.size_bytes,
                "start_seconds": item.segment_start_seconds,
                "end_seconds": item.segment_end_seconds,
            }
            for item in segments
        ]
        if (
            result.returncode != 0
            or final_session.status != "ended"
            or recipient.interval is not None
            or not media
            or any(not item["size_bytes"] for item in media)
        ):
            raise RuntimeError("recording session smoke 未形成完整的结束态与媒体分片")
        return {
            "ok": True,
            "schema_version": await database.schema_version(),
            "room_key": room_key,
            "session_id": session_id,
            "session_status": final_session.status,
            "recipient_session_status": recipient.session_status,
            "recipient_open_interval": recipient.interval is not None,
            "returncode": result.returncode,
            "stop_stage": result.stop_stage,
            "segment_rows": len(entries),
            "segments_synced": synced,
            "media": media,
            "contract_live_verified": contract.live_verified,
        }
    finally:
        await database.close()


def build_parser() -> argparse.ArgumentParser:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(
        description="使用本地 lavfi 验证 P1D recording/recipient Session 闭环"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "userdata" / "recording-session-smoke" / stamp,
    )
    parser.add_argument("--duration", type=float, default=2.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = asyncio.run(run_smoke(args.output_dir, duration_seconds=args.duration))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

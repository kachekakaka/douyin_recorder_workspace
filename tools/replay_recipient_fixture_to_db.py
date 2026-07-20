from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Database  # noqa: E402
from app.douyin import TARGET_METHOD  # noqa: E402
from app.douyin.envelope import EnvelopeDecodeError, inspect_frame  # noqa: E402
from app.douyin.recipient import (  # noqa: E402
    DecodedRecipientEvent,
    RecipientContract,
    RecipientDecodeError,
    decode_recipient_payload,
)
from app.douyin.replay import ReplayError, run_fixture  # noqa: E402
from app.sessions import RecipientSessionRepository, RecipientSessionService  # noqa: E402

DEFAULT_FIXTURE = ROOT / "tests" / "replay" / "fixtures" / "recipient-strict-unknown.synthetic.json"
DEFAULT_CONTRACT = ROOT / "tests" / "replay" / "contracts" / "recipient.synthetic-v1.json"
FIXTURE_ROOM_KEY = "fixture-room"
FIXTURE_SESSION_ID = "fixture-session"


def _required_int(step: dict[str, Any], key: str) -> int:
    value = step.get(key)
    if not isinstance(value, int):
        raise ReplayError(f"step.{key} 必须是整数")
    return value


def _required_text(step: dict[str, Any], key: str) -> str:
    value = step.get(key)
    if not isinstance(value, str) or not value:
        raise ReplayError(f"step.{key} 必须是非空字符串")
    return value


def _decode_base64(value: object) -> bytes:
    if not isinstance(value, str):
        raise ReplayError("frame_base64 必须是字符串")
    try:
        return base64.b64decode(value, validate=True)
    except (TypeError, ValueError) as exc:
        raise ReplayError("frame_base64 无效") from exc


def _raw_payload_json(event: DecodedRecipientEvent) -> str:
    return json.dumps(
        {
            "synthetic": True,
            "method": event.method,
            "msg_id": event.msg_id,
            "envelope_msg_id": event.envelope_msg_id,
            "recipient_user_id": event.recipient_user_id,
            "recipient_user_open_id": event.recipient_user_open_id,
            "change_reason_enum": event.change_reason_enum,
            "extra": event.extra,
            "unknown_fields": list(event.unknown_fields),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _public_report(
    *,
    fixture_name: str,
    fixture_result: object,
    schema_version: int,
    state: object,
    events: list[object],
    intervals: list[object],
    contract: RecipientContract,
) -> dict[str, object]:
    event_items = [event.to_public_dict() for event in events]
    interval_items = [interval.to_public_dict() for interval in intervals]
    report = {
        "fixture": fixture_name,
        "fixture_synthetic": True,
        "schema_version": schema_version,
        "contract_live_verified": contract.live_verified,
        "target_method": contract.target_method,
        "room_key": FIXTURE_ROOM_KEY,
        "session_id": FIXTURE_SESSION_ID,
        "state": state.to_public_dict(),
        "events": event_items,
        "intervals": interval_items,
        "summary": {
            "target_messages": fixture_result.target_messages,
            "unique_event_count": len(event_items),
            "duplicate_frame_count": sum(int(item["duplicate_count"]) for item in event_items),
            "late_event_count": sum(1 for item in event_items if item["is_late"]),
            "interval_count": len(interval_items),
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "raw_payload_json",
        "extra_json",
        "unknown_fields_json",
        "frame_base64",
    ):
        if forbidden in rendered:
            raise ReplayError(f"公开数据库 replay 报告泄漏字段: {forbidden}")
    return report


async def replay_fixture_to_database(
    fixture_path: Path,
    *,
    contract_path: Path = DEFAULT_CONTRACT,
    database_path: Path,
) -> dict[str, object]:
    try:
        document = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ReplayError(f"无法读取 replay fixture: {fixture_path}") from exc
    if not isinstance(document, dict) or document.get("synthetic") is not True:
        raise ReplayError("数据库 replay 只接受显式 synthetic fixture")
    if document.get("live_verified") is not False:
        raise ReplayError("synthetic fixture 必须保持 live_verified=false")
    steps = document.get("steps")
    if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
        raise ReplayError("fixture.steps 必须是对象数组")

    contract = RecipientContract.load(contract_path)
    if contract.live_verified:
        raise ReplayError("provisional contract 必须保持 live_verified=false")
    fixture_result = run_fixture(fixture_path, contract=contract)

    database = Database(database_path)
    await database.initialize()
    repository = RecipientSessionRepository(database)
    service = RecipientSessionService(repository, contract)
    try:
        runtime_ids = sorted(
            {
                str(step["runtime_instance_id"])
                for step in steps
                if isinstance(step.get("runtime_instance_id"), str)
                and step["runtime_instance_id"]
            }
        )
        for index, runtime_id in enumerate(runtime_ids, start=1):
            await database.execute(
                "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, ?, ?)",
                (runtime_id, "synthetic-replay", index),
            )
        await database.execute(
            """
            INSERT INTO rooms(
                room_key, room_url, enabled, quality, protocol,
                poll_interval_seconds, created_at_ms, updated_at_ms
            ) VALUES (?, ?, 1, 'origin', 'flv', 15, 1, 1)
            """,
            (FIXTURE_ROOM_KEY, "https://live.douyin.com/12345"),
        )

        started = False
        ended = False
        for raw_step in steps:
            step = dict(raw_step)
            step_type = _required_text(step, "type")
            if step_type == "session_started":
                if started:
                    raise ReplayError("fixture 只能开始一个场次")
                await service.start_session(
                    session_id=FIXTURE_SESSION_ID,
                    room_key=FIXTURE_ROOM_KEY,
                    started_at_ms=_required_int(step, "at_ms"),
                    started_monotonic_ns=_required_int(step, "monotonic_ns"),
                    runtime_instance_id=_required_text(step, "runtime_instance_id"),
                    external_room_id="12345",
                    title="synthetic recipient replay",
                )
                started = True
                continue
            if not started or ended:
                raise ReplayError(f"非法 fixture 步骤顺序: {step_type}")
            if step_type == "frame":
                try:
                    inspected = inspect_frame(_decode_base64(step.get("frame_base64")))
                except EnvelopeDecodeError as exc:
                    raise ReplayError(str(exc)) from exc
                for message in inspected.response.messages:
                    if message.method != TARGET_METHOD:
                        continue
                    try:
                        event = decode_recipient_payload(
                            message.payload,
                            contract=contract,
                            received_at_ms=_required_int(step, "received_at_ms"),
                            received_monotonic_ns=_required_int(
                                step, "received_monotonic_ns"
                            ),
                            runtime_instance_id=_required_text(
                                step, "runtime_instance_id"
                            ),
                            envelope_msg_id=message.msg_id,
                        )
                    except RecipientDecodeError as exc:
                        raise ReplayError("synthetic target payload 解码失败") from exc
                    await service.apply_event(
                        session_id=FIXTURE_SESSION_ID,
                        event=event,
                        raw_payload_json=_raw_payload_json(event),
                    )
                continue
            if step_type == "im_disconnected":
                await service.im_disconnected(
                    session_id=FIXTURE_SESSION_ID,
                    at_ms=_required_int(step, "at_ms"),
                    monotonic_ns=_required_int(step, "monotonic_ns"),
                    runtime_instance_id=_required_text(step, "runtime_instance_id"),
                )
                continue
            if step_type == "im_reconnected":
                await service.im_reconnected(session_id=FIXTURE_SESSION_ID)
                continue
            if step_type == "session_ended":
                await service.end_session(
                    session_id=FIXTURE_SESSION_ID,
                    at_ms=_required_int(step, "at_ms"),
                    monotonic_ns=_required_int(step, "monotonic_ns"),
                    runtime_instance_id=_required_text(step, "runtime_instance_id"),
                    end_reason="synthetic_fixture_ended",
                )
                ended = True
                continue
            raise ReplayError(f"数据库 replay 暂不支持 step.type: {step_type}")

        if not ended:
            raise ReplayError("fixture 未结束场次")
        state = await repository.get_state(FIXTURE_ROOM_KEY)
        events = await repository.list_events(
            room_key=FIXTURE_ROOM_KEY,
            session_id=FIXTURE_SESSION_ID,
            limit=500,
        )
        intervals = await repository.list_intervals(
            room_key=FIXTURE_ROOM_KEY,
            session_id=FIXTURE_SESSION_ID,
            limit=500,
        )
        compact_intervals = [
            {
                "status": item.status,
                "reason": item.reason,
                "recipient_key": item.recipient_key,
                "started_at_ms": item.started_at_ms,
                "ended_at_ms": item.ended_at_ms,
            }
            for item in intervals
        ]
        expected_intervals = fixture_result.reducer_snapshot.get("intervals")
        if not isinstance(expected_intervals, list):
            raise ReplayError("reducer replay 未返回 intervals")
        expected_compact = [
            {
                "status": item.get("status"),
                "reason": item.get("reason"),
                "recipient_key": item.get("recipient_key"),
                "started_at_ms": item.get("started_at_ms"),
                "ended_at_ms": item.get("ended_at_ms"),
            }
            for item in expected_intervals
            if isinstance(item, dict)
        ]
        if compact_intervals != expected_compact:
            raise ReplayError("SQLite interval 投影与 reducer replay 不一致")
        return _public_report(
            fixture_name=str(document.get("name") or fixture_path.stem),
            fixture_result=fixture_result,
            schema_version=await database.schema_version(),
            state=state,
            events=events,
            intervals=intervals,
            contract=contract,
        )
    finally:
        await database.close()


async def async_main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay a synthetic recipient fixture into temporary SQLite"
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    temporary: tempfile.TemporaryDirectory[str] | None = None
    database_path = args.database
    if database_path is None:
        temporary = tempfile.TemporaryDirectory(prefix="recipient-db-replay-")
        database_path = Path(temporary.name) / "recipient.db"
    try:
        report = await replay_fixture_to_database(
            args.fixture,
            contract_path=args.contract,
            database_path=database_path,
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    finally:
        if temporary is not None:
            temporary.cleanup()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())

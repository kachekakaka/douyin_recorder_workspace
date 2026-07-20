from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.db import Database
from app.douyin import TARGET_METHOD
from app.douyin.recipient import (
    DecodedRecipientEvent,
    RecipientContract,
    canonical_recipient_key,
)
from app.sessions import (
    RecipientSessionRepository,
    RecipientSessionService,
    RecipientSessionStateError,
)


def _event(
    *,
    msg_id: str,
    at_ms: int,
    monotonic_ns: int,
    recipient_id: str | None,
    runtime: str = "runtime-a",
) -> DecodedRecipientEvent:
    return DecodedRecipientEvent(
        method=TARGET_METHOD,
        msg_id=msg_id,
        envelope_msg_id=msg_id,
        server_event_at_ms=at_ms - 5,
        server_time_unit="fixture",
        received_at_ms=at_ms,
        received_monotonic_ns=monotonic_ns,
        runtime_instance_id=runtime,
        recipient_user_id=recipient_id,
        recipient_user_open_id=None,
        recipient_key=canonical_recipient_key(recipient_id, None),
        change_reason_enum=2,
        extra={"synthetic": "true"},
        payload_hash=msg_id.rjust(64, "0")[-64:],
        payload_size=8,
        unknown_fields=(),
        dedup_key=f"msg:{msg_id}",
    )


async def _setup(
    database: Database, contract_path: Path
) -> tuple[RecipientSessionService, RecipientSessionRepository]:
    await database.initialize()
    await database.execute(
        "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, ?, ?)",
        ("runtime-a", "test", 1),
    )
    await database.execute(
        "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, ?, ?)",
        ("runtime-b", "test", 2),
    )
    await database.execute(
        """
        INSERT INTO rooms(
            room_key, room_url, enabled, quality, protocol,
            poll_interval_seconds, created_at_ms, updated_at_ms
        ) VALUES (?, ?, 1, 'origin', 'flv', 15, 1, 1)
        """,
        ("room-a", "https://live.douyin.com/12345"),
    )
    contract = RecipientContract.load(contract_path)
    repository = RecipientSessionRepository(database)
    return RecipientSessionService(repository, contract), repository


def test_transactional_recipient_timeline_and_privacy(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "userdata" / "test.db")
        contract_path = Path("app/douyin/contracts/provisional_v1.json")
        service, repository = await _setup(database, contract_path)
        try:
            state = await service.start_session(
                session_id="session-a",
                room_key="room-a",
                started_at_ms=1000,
                started_monotonic_ns=1_000_000,
                runtime_instance_id="runtime-a",
            )
            assert state.interval is not None
            assert state.interval.status == "waiting"
            assert state.protocol_live_verified is False

            first = await service.apply_event(
                session_id="session-a",
                event=_event(
                    msg_id="1",
                    at_ms=1100,
                    monotonic_ns=1_100_000,
                    recipient_id="90071992547409931",
                ),
                raw_payload_json=json.dumps({"synthetic": True, "private": "not-public"}),
            )
            assert first.outcome == "active"
            assert first.state.interval is not None
            assert first.state.interval.recipient_user_id == "90071992547409931"

            duplicate = await service.apply_event(
                session_id="session-a",
                event=_event(
                    msg_id="1",
                    at_ms=1120,
                    monotonic_ns=1_120_000,
                    recipient_id="90071992547409931",
                ),
                raw_payload_json="{}",
            )
            assert duplicate.outcome == "duplicate"
            assert duplicate.event.duplicate_count == 1
            assert duplicate.event.last_received_at_ms == 1120

            same = await service.apply_event(
                session_id="session-a",
                event=_event(
                    msg_id="2",
                    at_ms=1200,
                    monotonic_ns=1_200_000,
                    recipient_id="90071992547409931",
                ),
                raw_payload_json="{}",
            )
            assert same.outcome == "same-recipient"

            switched = await service.apply_event(
                session_id="session-a",
                event=_event(
                    msg_id="3",
                    at_ms=1300,
                    monotonic_ns=1_300_000,
                    recipient_id="90071992547409932",
                ),
                raw_payload_json="{}",
            )
            assert switched.outcome == "active"

            empty = await service.apply_event(
                session_id="session-a",
                event=_event(
                    msg_id="4",
                    at_ms=1400,
                    monotonic_ns=1_400_000,
                    recipient_id=None,
                ),
                raw_payload_json="{}",
            )
            assert empty.outcome == "unknown"
            assert empty.state.interval is not None
            assert empty.state.interval.reason == "empty_recipient"

            disconnected = await service.im_disconnected(
                session_id="session-a",
                at_ms=1500,
                monotonic_ns=1_500_000,
                runtime_instance_id="runtime-a",
            )
            assert disconnected.interval is not None
            assert disconnected.interval.reason == "im_disconnected"
            reconnected = await service.im_reconnected(session_id="session-a")
            assert reconnected.interval is not None
            assert reconnected.interval.reason == "im_disconnected"

            after_reconnect = await service.apply_event(
                session_id="session-a",
                event=_event(
                    msg_id="5",
                    at_ms=1700,
                    monotonic_ns=1_700_000,
                    recipient_id="90071992547409933",
                ),
                raw_payload_json="{}",
            )
            assert after_reconnect.outcome == "active"

            late = await service.apply_event(
                session_id="session-a",
                event=_event(
                    msg_id="6",
                    at_ms=1650,
                    monotonic_ns=1_710_000,
                    recipient_id="90071992547409934",
                ),
                raw_payload_json="{}",
            )
            assert late.outcome == "late"
            assert late.event.is_late is True
            assert late.state.interval is not None
            assert late.state.interval.recipient_user_id == "90071992547409933"

            events = await repository.list_events(room_key="room-a")
            intervals = await repository.list_intervals(room_key="room-a")
            assert len(events) == 6
            assert [item.status for item in intervals] == [
                "waiting",
                "active",
                "active",
                "unknown",
                "unknown",
                "active",
            ]
            rendered = json.dumps(
                {
                    "events": [event.to_public_dict() for event in events],
                    "intervals": [interval.to_public_dict() for interval in intervals],
                },
                sort_keys=True,
            )
            assert "not-public" not in rendered
            assert "raw_payload_json" not in rendered
            assert "extra_json" not in rendered
            assert "unknown_fields_json" not in rendered

            await service.end_session(
                session_id="session-a",
                at_ms=1800,
                monotonic_ns=9,
                runtime_instance_id="runtime-b",
                end_reason="synthetic_end",
            )
            closed = await repository.list_intervals(room_key="room-a")
            assert closed[-1].ended_runtime_instance_id == "runtime-b"
            assert closed[-1].ended_monotonic_ns is None
            final = await repository.get_state("room-a")
            assert final.session_status == "ended"
            assert final.interval is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_recipient_repository_rejects_invalid_transitions(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "userdata" / "test.db")
        service, _repository = await _setup(
            database, Path("app/douyin/contracts/provisional_v1.json")
        )
        try:
            await service.start_session(
                session_id="session-a",
                room_key="room-a",
                started_at_ms=1000,
                started_monotonic_ns=100,
                runtime_instance_id="runtime-a",
            )
            with pytest.raises(RecipientSessionStateError):
                await service.im_disconnected(
                    session_id="session-a",
                    at_ms=900,
                    monotonic_ns=101,
                    runtime_instance_id="runtime-a",
                )
            with pytest.raises(RecipientSessionStateError):
                await service.apply_event(
                    session_id="session-a",
                    event=_event(
                        msg_id="1",
                        at_ms=1100,
                        monotonic_ns=110,
                        recipient_id="1",
                    ),
                    raw_payload_json="not-json",
                )
        finally:
            await database.close()

    asyncio.run(scenario())

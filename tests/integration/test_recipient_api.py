from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.state as state_module
from app.douyin import TARGET_METHOD
from app.douyin.recipient import DecodedRecipientEvent, canonical_recipient_key
from app.main import create_app
from app.paths import ROOT, RuntimePaths
from app.runtime import ToolStatus
from app.settings import Settings
from app.state import AppState


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "config.json.default").write_text(
        json.dumps({"server": {"host": "127.0.0.1", "port": 3399, "auth_required": False}}),
        encoding="utf-8",
    )
    return Settings.load(
        root=tmp_path,
        paths=RuntimePaths.defaults(tmp_path),
        environ={
            "DOUYIN_RECORDER_PROTOCOL_CONTRACT": str(
                ROOT / "app" / "douyin" / "contracts" / "provisional_v1.json"
            )
        },
    )


def _event(runtime_instance_id: str) -> DecodedRecipientEvent:
    recipient_id = "90071992547409931"
    return DecodedRecipientEvent(
        method=TARGET_METHOD,
        msg_id="1001",
        envelope_msg_id="1001",
        server_event_at_ms=1_095,
        server_time_unit="fixture",
        received_at_ms=1_100,
        received_monotonic_ns=1_100_000,
        runtime_instance_id=runtime_instance_id,
        recipient_user_id=recipient_id,
        recipient_user_open_id=None,
        recipient_key=canonical_recipient_key(recipient_id, None),
        change_reason_enum=2,
        extra={"private": "EXTRA-PRIVATE"},
        payload_hash="a" * 64,
        payload_size=32,
        unknown_fields=(),
        dedup_key="msg:1001",
    )


def test_recipient_read_apis_are_room_scoped_and_redacted(tmp_path: Path, monkeypatch) -> None:
    async def fake_check_tool(name: str, configured: str, *, timeout: float = 5.0):
        del timeout
        return ToolStatus(name, configured, configured, True, f"{name} test version", "")

    monkeypatch.setattr(state_module, "check_tool", fake_check_tool)
    state = AppState.create(_settings(tmp_path))
    app = create_app(state=state)

    with TestClient(app, base_url="http://127.0.0.1:3399") as client:
        for room_key, room_id in (("group-a", "73504089679"), ("group-b", "73504089680")):
            created = client.post(
                "/api/rooms",
                json={"room_key": room_key, "room_url": room_id},
            )
            assert created.status_code == 201

        async def seed() -> None:
            await state.recipient_service.start_session(
                session_id="session-a",
                room_key="group-a",
                started_at_ms=1_000,
                started_monotonic_ns=1_000_000,
                runtime_instance_id=state.runtime_instance_id,
            )
            await state.recipient_service.apply_event(
                session_id="session-a",
                event=_event(state.runtime_instance_id),
                raw_payload_json=json.dumps(
                    {"private": "RAW-PRIVATE", "recipient": "REAL-NAME-NOT-PUBLIC"}
                ),
            )

        assert client.portal is not None
        client.portal.call(seed)

        state_response = client.get("/api/rooms/group-a/recipient-state")
        assert state_response.status_code == 200
        current = state_response.json()["data"]["current"]
        assert current["status"] == "active"
        assert current["recipient_user_id"] == "90071992547409931"
        assert isinstance(current["recipient_user_id"], str)
        assert state_response.json()["data"]["protocol_live_verified"] is False

        events_response = client.get("/api/rooms/group-a/recipient-events")
        assert events_response.status_code == 200
        events = events_response.json()["data"]["items"]
        assert len(events) == 1
        assert events[0]["recipient_user_id"] == "90071992547409931"

        intervals_response = client.get("/api/rooms/group-a/recipient-intervals")
        assert intervals_response.status_code == 200
        assert [item["status"] for item in intervals_response.json()["data"]["items"]] == [
            "waiting",
            "active",
        ]

        rendered = json.dumps(
            {
                "state": state_response.json(),
                "events": events_response.json(),
                "intervals": intervals_response.json(),
            },
            sort_keys=True,
        )
        for forbidden in (
            "RAW-PRIVATE",
            "REAL-NAME-NOT-PUBLIC",
            "EXTRA-PRIVATE",
            "raw_payload_json",
            "extra_json",
            "unknown_fields_json",
        ):
            assert forbidden not in rendered

        empty_state = client.get("/api/rooms/group-b/recipient-state")
        assert empty_state.status_code == 200
        assert empty_state.json()["data"]["session_id"] is None
        assert empty_state.json()["data"]["current"] is None

        cross_room = client.get(
            "/api/rooms/group-b/recipient-events",
            params={"session_id": "session-a"},
        )
        assert cross_room.status_code == 404

        assert client.get("/api/rooms/missing/recipient-state").status_code == 404
        assert client.get("/api/rooms/INVALID/recipient-state").status_code == 422
        assert client.get(
            "/api/rooms/group-a/recipient-events", params={"limit": 0}
        ).status_code == 422

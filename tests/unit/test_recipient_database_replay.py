from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tools.replay_recipient_fixture_to_db import (
    DEFAULT_FIXTURE,
    replay_fixture_to_database,
)


def test_recipient_database_replay_is_deterministic_and_redacted(tmp_path: Path) -> None:
    async def scenario() -> None:
        first = await replay_fixture_to_database(
            DEFAULT_FIXTURE,
            database_path=tmp_path / "first.db",
        )
        second = await replay_fixture_to_database(
            DEFAULT_FIXTURE,
            database_path=tmp_path / "second.db",
        )
        assert first == second
        assert first["schema_version"] == 6
        assert first["contract_live_verified"] is False
        assert first["state"]["session_status"] == "ended"
        assert first["state"]["current"] is None
        assert first["summary"] == {
            "target_messages": 7,
            "unique_event_count": 6,
            "duplicate_frame_count": 1,
            "late_event_count": 1,
            "interval_count": 7,
        }
        assert [item["status"] for item in first["intervals"]] == [
            "waiting",
            "active",
            "active",
            "unknown",
            "active",
            "unknown",
            "active",
        ]
        rendered = json.dumps(first, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            "raw_payload_json",
            "extra_json",
            "unknown_fields_json",
            "frame_base64",
        ):
            assert forbidden not in rendered
        recipient_ids = [
            item["recipient_user_id"]
            for item in first["events"]
            if item["recipient_user_id"] is not None
        ]
        assert recipient_ids
        assert all(isinstance(value, str) for value in recipient_ids)

    asyncio.run(scenario())

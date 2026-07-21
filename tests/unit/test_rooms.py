from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.db import Database
from app.rooms import RoomAlreadyExistsError, RoomCreate, RoomPatch, RoomRepository


def test_room_repository_crud_and_check_audit(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "userdata" / "douyin_recorder.db")
        await database.initialize()
        repository = RoomRepository(database)
        room = await repository.create_room(RoomCreate(room_key="group-a", room_url="73504089679"))
        assert room.room_url == "https://live.douyin.com/73504089679"
        assert room.enabled is True

        with pytest.raises(RoomAlreadyExistsError):
            await repository.create_room(RoomCreate(room_key="group-a", room_url="73504089679"))

        updated = await repository.update_room(
            "group-a",
            RoomPatch(quality="hd", protocol="hls", poll_interval_seconds=30),
        )
        assert updated.quality == "hd"
        assert updated.protocol == "hls"
        assert updated.poll_interval_seconds == 30

        check = {
            "checked_at_ms": 1234,
            "live_state": "live",
            "http_status": 200,
            "final_host": "live.douyin.com",
            "final_path": "/73504089679",
            "external_room_id": "998877665544332211",
            "web_rid": "73504089679",
            "title": "Fixture",
            "stream_candidate_count": 2,
            "stream_candidates": [],
        }
        await repository.record_check("group-a", check)
        rooms = await repository.list_rooms()
        assert len(rooms) == 1
        assert rooms[0].latest_check == check
        assert await database.schema_version() == 6
        await database.close()

    asyncio.run(scenario())


def test_room_check_audit_rejects_secret_bearing_fields(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "userdata" / "douyin_recorder.db")
        await database.initialize()
        repository = RoomRepository(database)
        await repository.create_room(RoomCreate(room_key="group-a", room_url="73504089679"))

        with pytest.raises(ValueError, match="禁止持久化"):
            await repository.record_check(
                "group-a",
                {
                    "live_state": "live",
                    "stream_candidates": [
                        {"url": "https://pull.example.invalid/live.flv?signature=SECRET"}
                    ],
                },
            )
        with pytest.raises(ValueError, match="query"):
            await repository.record_check(
                "group-a",
                {
                    "live_state": "live",
                    "room_url": "https://live.douyin.com/73504089679?signature=SECRET",
                },
            )
        rows = await database.fetch_all("SELECT detail_json FROM room_checks")
        assert rows == []
        await database.close()

    asyncio.run(scenario())


def test_room_repository_serializes_duplicate_url_creation(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "userdata" / "douyin_recorder.db")
        await database.initialize()
        repository = RoomRepository(database)

        results = await asyncio.gather(
            repository.create_room(RoomCreate(room_key="group-a", room_url="73504089679")),
            repository.create_room(RoomCreate(room_key="group-b", room_url="73504089679")),
            return_exceptions=True,
        )
        created = [item for item in results if not isinstance(item, Exception)]
        rejected = [item for item in results if isinstance(item, RoomAlreadyExistsError)]
        assert len(created) == 1
        assert len(rejected) == 1
        assert len(await repository.list_rooms()) == 1
        await database.close()

    asyncio.run(scenario())

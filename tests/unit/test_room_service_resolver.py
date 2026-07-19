from __future__ import annotations

import asyncio

from app.db import Database
from app.douyin.live_page import inspect_live_page, stream_candidate_from_url
from app.rooms import RoomCreate, RoomPatch, RoomRepository, RoomService


class FakeResolver:
    def __init__(self) -> None:
        self.discarded: list[str] = []
        self.closed = False

    async def resolve(self, room_reference: str):
        candidate = stream_candidate_from_url(
            "https://pull.example.douyincdn.com/live/test.flv?sign=SECRET",
            source_path="fixture",
        )
        assert candidate is not None
        return inspect_live_page(
            (
                b'{"stream_url":{"flv":"https://pull.example.douyincdn.com/'
                b'live/test.flv?sign=SECRET"}}'
            ),
            room_url=room_reference,
            http_status=200,
            final_url=room_reference,
        )

    def discard(self, room_reference: str) -> None:
        self.discarded.append(room_reference)

    async def close(self) -> None:
        self.closed = True


def test_room_service_discards_private_candidates_on_change_and_disable(tmp_path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "userdata" / "rooms.db")
        await database.initialize()
        repository = RoomRepository(database)
        resolver = FakeResolver()
        service = RoomService(repository, resolver)  # type: ignore[arg-type]
        await service.create_room(RoomCreate(room_key="group-a", room_url="79907888978"))
        await service.check_room("group-a")
        await service.update_room(
            "group-a",
            RoomPatch(room_url="94771623313"),
        )
        assert resolver.discarded == ["https://live.douyin.com/79907888978"]
        await service.set_enabled("group-a", False)
        assert resolver.discarded[-1] == "https://live.douyin.com/94771623313"
        await service.close()
        assert resolver.closed is True
        await database.close()

    asyncio.run(scenario())

from __future__ import annotations

import asyncio
from collections import defaultdict

from app.douyin.live_page import DouyinLivePageClient, LivePageResult
from app.rooms.models import RoomCreate, RoomPatch, RoomRecord
from app.rooms.repository import RoomRepository


class RoomService:
    def __init__(
        self,
        repository: RoomRepository,
        live_page_client: DouyinLivePageClient,
    ) -> None:
        self.repository = repository
        self.live_page_client = live_page_client
        self._check_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def close(self) -> None:
        await self.live_page_client.close()

    async def list_rooms(self) -> list[RoomRecord]:
        return await self.repository.list_rooms()

    async def get_room(self, room_key: str) -> RoomRecord:
        return await self.repository.get_room(room_key)

    async def create_room(self, payload: RoomCreate) -> RoomRecord:
        return await self.repository.create_room(payload)

    async def update_room(self, room_key: str, patch: RoomPatch) -> RoomRecord:
        return await self.repository.update_room(room_key, patch)

    async def set_enabled(self, room_key: str, enabled: bool) -> RoomRecord:
        return await self.repository.set_enabled(room_key, enabled)

    async def check_room(self, room_key: str) -> LivePageResult:
        async with self._check_locks[room_key]:
            room = await self.repository.get_room(room_key)
            result = await self.live_page_client.check(room.room_url)
            await self.repository.record_check(room_key, result.snapshot.to_public_dict())
            return result

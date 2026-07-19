from __future__ import annotations

import asyncio
from collections import defaultdict

from app.douyin.live_page import LivePageResult
from app.douyin.stream_resolver import DouyinStreamResolver
from app.rooms.models import RoomCreate, RoomPatch, RoomRecord
from app.rooms.repository import RoomRepository


class RoomService:
    def __init__(
        self,
        repository: RoomRepository,
        stream_resolver: DouyinStreamResolver,
    ) -> None:
        self.repository = repository
        self.stream_resolver = stream_resolver
        self._check_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def close(self) -> None:
        await self.stream_resolver.close()

    async def list_rooms(self) -> list[RoomRecord]:
        return await self.repository.list_rooms()

    async def get_room(self, room_key: str) -> RoomRecord:
        return await self.repository.get_room(room_key)

    async def create_room(self, payload: RoomCreate) -> RoomRecord:
        return await self.repository.create_room(payload)

    async def update_room(self, room_key: str, patch: RoomPatch) -> RoomRecord:
        previous = await self.repository.get_room(room_key)
        updated = await self.repository.update_room(room_key, patch)
        if previous.room_url != updated.room_url:
            self.stream_resolver.discard(previous.room_url)
        if not updated.enabled:
            self.stream_resolver.discard(updated.room_url)
        return updated

    async def set_enabled(self, room_key: str, enabled: bool) -> RoomRecord:
        room = await self.repository.set_enabled(room_key, enabled)
        if not enabled:
            self.stream_resolver.discard(room.room_url)
        return room

    async def check_room(self, room_key: str) -> LivePageResult:
        async with self._check_locks[room_key]:
            room = await self.repository.get_room(room_key)
            result = await self.stream_resolver.resolve(room.room_url)
            await self.repository.record_check(room_key, result.snapshot.to_public_dict())
            return result

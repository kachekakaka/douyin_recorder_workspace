from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from app import __version__
from app.db import Database
from app.douyin.live_page import DouyinLivePageClient
from app.douyin.recipient import RecipientContract
from app.douyin.stream_resolver import DouyinStreamResolver
from app.rooms import RoomRepository, RoomService
from app.runtime import ToolStatus, check_tool
from app.settings import Settings


@dataclass(slots=True)
class AppState:
    settings: Settings
    database: Database
    protocol_contract: RecipientContract
    room_repository: RoomRepository
    room_service: RoomService
    runtime_instance_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    ffmpeg: ToolStatus | None = None
    ffprobe: ToolStatus | None = None

    @classmethod
    def create(
        cls,
        settings: Settings,
        *,
        live_page_client: DouyinLivePageClient | None = None,
        stream_resolver: DouyinStreamResolver | None = None,
    ) -> AppState:
        contract = RecipientContract.load(settings.protocol_contract_path)
        database = Database(settings.paths.database_path)
        room_repository = RoomRepository(database)
        resolver = stream_resolver or DouyinStreamResolver(
            live_page_client or DouyinLivePageClient()
        )
        room_service = RoomService(room_repository, resolver)
        return cls(
            settings=settings,
            database=database,
            protocol_contract=contract,
            room_repository=room_repository,
            room_service=room_service,
        )

    async def start(self) -> None:
        await self.database.initialize()
        await self.database.execute(
            "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, ?, ?)",
            (self.runtime_instance_id, __version__, self.started_at_ms),
        )
        await self.refresh_tools()

    async def refresh_tools(self) -> None:
        self.ffmpeg, self.ffprobe = await asyncio.gather(
            check_tool("ffmpeg", self.settings.ffmpeg_path),
            check_tool("ffprobe", self.settings.ffprobe_path),
        )

    async def readiness(self, *, refresh: bool = False) -> dict[str, object]:
        if refresh:
            await self.refresh_tools()
        schema_version = await self.database.schema_version()
        ffmpeg = self.ffmpeg or await check_tool("ffmpeg", self.settings.ffmpeg_path)
        ffprobe = self.ffprobe or await check_tool("ffprobe", self.settings.ffprobe_path)
        rooms = await self.room_repository.list_rooms()
        ready = schema_version > 0 and ffmpeg.ready and ffprobe.ready
        return {
            "ready": ready,
            "runtime_instance_id": self.runtime_instance_id,
            "started_at_ms": self.started_at_ms,
            "schema_version": schema_version,
            "database_path": str(self.settings.paths.database_path),
            "records_path": str(self.settings.paths.records_dir),
            "room_count": len(rooms),
            "enabled_room_count": sum(1 for room in rooms if room.enabled),
            "ffmpeg": ffmpeg.to_dict(),
            "ffprobe": ffprobe.to_dict(),
            "protocol_contract": self.protocol_contract.to_public_dict(),
        }

    async def stop(self) -> None:
        ended_at_ms = int(time.time() * 1000)
        try:
            await self.database.execute(
                "UPDATE runtime_instances SET ended_at_ms = ? WHERE id = ?",
                (ended_at_ms, self.runtime_instance_id),
            )
        finally:
            try:
                await self.room_service.close()
            finally:
                await self.database.close()

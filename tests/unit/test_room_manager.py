from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from types import SimpleNamespace

from app.manager import RoomManager
from app.rooms import RoomRecord


def _room(room_key: str, *, enabled: bool = True, poll: int = 5) -> RoomRecord:
    return RoomRecord(
        room_key=room_key,
        room_url=f"https://live.douyin.com/{room_key}",
        enabled=enabled,
        quality="origin",
        protocol="flv",
        poll_interval_seconds=poll,
        created_at_ms=1,
        updated_at_ms=1,
    )


class FakeRoomService:
    def __init__(self, rooms: list[RoomRecord]) -> None:
        self.rooms = {item.room_key: item for item in rooms}
        self.states: defaultdict[str, deque[str | BaseException]] = defaultdict(deque)
        self.check_calls: defaultdict[str, int] = defaultdict(int)
        self.active_checks = 0
        self.max_active_checks = 0
        self.check_gate: asyncio.Event | None = None

    async def list_rooms(self) -> list[RoomRecord]:
        return list(self.rooms.values())

    async def get_room(self, room_key: str) -> RoomRecord:
        return self.rooms[room_key]

    async def check_room(self, room_key: str):
        self.check_calls[room_key] += 1
        self.active_checks += 1
        self.max_active_checks = max(self.max_active_checks, self.active_checks)
        try:
            if self.check_gate is not None:
                await self.check_gate.wait()
            value = self.states[room_key].popleft() if self.states[room_key] else "unknown"
            if isinstance(value, BaseException):
                raise value
            return SimpleNamespace(snapshot=SimpleNamespace(live_state=value))
        finally:
            self.active_checks -= 1


class FakeRecordingService:
    def __init__(self) -> None:
        self.active: defaultdict[str, bool] = defaultdict(bool)
        self.start_calls: defaultdict[str, int] = defaultdict(int)
        self.stop_calls: list[tuple[str, str]] = []
        self.start_error: BaseException | None = None

    async def get_state(self, room_key: str):
        return SimpleNamespace(active=self.active[room_key])

    async def start_recording(self, room_key: str):
        self.start_calls[room_key] += 1
        if self.start_error is not None:
            raise self.start_error
        self.active[room_key] = True
        return await self.get_state(room_key)

    async def stop_recording(self, room_key: str, *, reason: str):
        self.stop_calls.append((room_key, reason))
        self.active[room_key] = False
        return await self.get_state(room_key)


def _manager(
    rooms: FakeRoomService,
    recording: FakeRecordingService,
    *,
    offline_confirmations: int = 2,
    max_parallel_checks: int = 2,
) -> RoomManager:
    return RoomManager(
        room_service=rooms,  # type: ignore[arg-type]
        recording_service=recording,  # type: ignore[arg-type]
        enabled=True,
        jitter_seconds=0,
        offline_confirmations=offline_confirmations,
        max_parallel_checks=max_parallel_checks,
        wall_time_ms=lambda: 10_000,
        random_uniform=lambda _low, _high: 0.0,
    )


def test_live_starts_once_and_offline_threshold_stops() -> None:
    async def scenario() -> None:
        rooms = FakeRoomService([_room("room-a")])
        recording = FakeRecordingService()
        manager = _manager(rooms, recording)
        rooms.states["room-a"].extend(["live", "live", "offline", "offline"])

        first = await manager.run_once("room-a")
        second = await manager.run_once("room-a")
        pending = await manager.run_once("room-a")
        stopped = await manager.run_once("room-a")

        assert first.recording_active is True
        assert second.recording_active is True
        assert recording.start_calls["room-a"] == 1
        assert pending.lifecycle == "offline_pending"
        assert pending.recording_active is True
        assert stopped.lifecycle == "offline"
        assert stopped.recording_active is False
        assert recording.stop_calls == [("room-a", "confirmed_offline")]
        await manager.close()

    asyncio.run(scenario())


def test_unknown_blocked_and_error_keep_existing_recording_and_backoff() -> None:
    async def scenario() -> None:
        rooms = FakeRoomService([_room("room-a")])
        recording = FakeRecordingService()
        recording.active["room-a"] = True
        manager = _manager(rooms, recording)
        rooms.states["room-a"].extend(["unknown", "blocked", "error", RuntimeError("secret")])

        states = [await manager.run_once("room-a") for _ in range(4)]

        assert all(item.recording_active for item in states)
        assert recording.stop_calls == []
        assert [item.lifecycle for item in states[:3]] == ["unknown", "blocked", "error"]
        assert states[-1].last_error_code == "runtimeerror"
        assert "secret" not in repr(states[-1].to_public_dict())
        assert states[-1].next_check_at_ms is not None
        assert states[-1].next_check_at_ms > 10_000 + 5_000
        await manager.close()

    asyncio.run(scenario())


def test_global_check_semaphore_and_room_failure_isolation() -> None:
    async def scenario() -> None:
        rooms = FakeRoomService([_room("room-a"), _room("room-b"), _room("room-c")])
        recording = FakeRecordingService()
        manager = _manager(rooms, recording, max_parallel_checks=2)
        gate = asyncio.Event()
        rooms.check_gate = gate
        rooms.states["room-a"].append(RuntimeError("a-private-error"))
        rooms.states["room-b"].append("live")
        rooms.states["room-c"].append("live")

        tasks = [asyncio.create_task(manager.run_once(key)) for key in rooms.rooms]
        for _ in range(50):
            if rooms.active_checks == 2:
                break
            await asyncio.sleep(0)
        assert rooms.max_active_checks == 2
        gate.set()
        results = await asyncio.gather(*tasks)

        values = {item.room_key: item for item in results}
        assert values["room-a"].lifecycle == "error"
        assert values["room-b"].recording_active is True
        assert values["room-c"].recording_active is True
        assert recording.start_calls["room-b"] == 1
        assert recording.start_calls["room-c"] == 1
        await manager.close()

    asyncio.run(scenario())


def test_reconcile_tracks_enabled_rooms_and_stops_disabled_recording() -> None:
    async def scenario() -> None:
        room_a = _room("room-a")
        room_b = _room("room-b", enabled=False)
        rooms = FakeRoomService([room_a, room_b])
        recording = FakeRecordingService()
        recording.active["room-a"] = True
        manager = _manager(rooms, recording)

        status = await manager.start()
        assert status.running is True
        assert status.worker_count == 1
        assert status.workers[0].room_key == "room-a"

        rooms.rooms["room-a"] = room_a.model_copy(update={"enabled": False})
        rooms.rooms["room-b"] = room_b.model_copy(update={"enabled": True})
        status = await manager.reconcile()

        assert {item.room_key for item in status.workers} == {"room-b"}
        assert ("room-a", "room_disabled") in recording.stop_calls
        await manager.close()

    asyncio.run(scenario())

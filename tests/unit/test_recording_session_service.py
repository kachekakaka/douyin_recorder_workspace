from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.douyin.live_page import inspect_live_page
from app.media import ProgressSnapshot, RecorderResult
from app.paths import ROOT, RuntimePaths
from app.recording import RecordingSessionRepository
from app.rooms import RoomCreate
from app.runtime import ToolStatus
from app.settings import Settings
from app.state import AppState

FIXTURE = ROOT / "tests" / "fixtures" / "douyin" / "live-page.synthetic.html"


class FakeLivePageClient:
    async def check(self, room_reference: str):
        return inspect_live_page(
            FIXTURE.read_bytes(),
            room_url=room_reference,
            http_status=200,
            final_url=room_reference,
        )

    async def close(self) -> None:
        return None


class FakeSupervisor:
    def __init__(self, spec, *, on_progress=None, on_stderr=None) -> None:
        del on_stderr
        self.spec = spec
        self.on_progress = on_progress
        self.running = False
        self._future: asyncio.Future[RecorderResult] = asyncio.get_running_loop().create_future()

    async def start(self) -> None:
        self.running = True

    async def wait(self) -> RecorderResult:
        return await self._future

    async def stop(self, *, graceful_timeout: float = 10.0, terminate_timeout: float = 5.0):
        del graceful_timeout, terminate_timeout
        if not self._future.done():
            self.finish(stop_stage="graceful")
        return await self._future

    def finish(self, *, returncode: int = 0, stop_stage: str = "natural") -> None:
        self.running = False
        if not self._future.done():
            self._future.set_result(
                RecorderResult(
                    started_at_ms=1_000,
                    ended_at_ms=2_000,
                    returncode=returncode,
                    stop_stage=stop_stage,
                    last_progress=ProgressSnapshot(
                        received_at_ms=1_500,
                        frame=25,
                        out_time_us=1_000_000,
                        progress="end",
                    ),
                    stderr_lines=0,
                    callback_error_count=0,
                    redacted_argv=self.spec.redacted_argv,
                )
            )


class FakeSupervisorFactory:
    def __init__(self) -> None:
        self.instances: list[FakeSupervisor] = []

    def __call__(self, spec, *, on_progress=None, on_stderr=None) -> FakeSupervisor:
        instance = FakeSupervisor(spec, on_progress=on_progress, on_stderr=on_stderr)
        self.instances.append(instance)
        return instance


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


def test_single_room_recording_uses_shared_recipient_session_and_segments(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        async def fake_check_tool(name: str, configured: str, *, timeout: float = 5.0):
            del timeout
            return ToolStatus(name, configured, configured, True, f"{name} test", "")

        monkeypatch.setattr("app.state.check_tool", fake_check_tool)
        factory = FakeSupervisorFactory()
        state = AppState.create(
            _settings(tmp_path),
            live_page_client=FakeLivePageClient(),  # type: ignore[arg-type]
            recording_supervisor_factory=factory,
        )
        await state.start()
        try:
            await state.room_service.create_room(
                RoomCreate(room_key="group-a", room_url="73504089679")
            )
            started = await state.recording_service.start_recording("group-a")
            assert started.active is True
            assert started.session is not None
            session_id = started.session.id
            assert started.session.input_host.endswith("douyincdn.com")
            rendered = json.dumps(started.to_public_dict(), sort_keys=True)
            assert "SECRET" not in rendered
            assert "signature=" not in rendered

            recipient = await state.recipient_repository.get_state("group-a")
            assert recipient.session_id == session_id
            assert recipient.interval is not None
            assert recipient.interval.status == "waiting"

            fake = factory.instances[0]
            media_dir = fake.spec.cwd / "media"
            (media_dir / "00000.mkv").write_bytes(b"media")
            (media_dir / "segments.csv").write_text(
                "00000.mkv,0.000000,1.000000\n00001.mkv,1.000000",
                encoding="utf-8",
            )
            assert fake.on_progress is not None
            await fake.on_progress(
                ProgressSnapshot(
                    received_at_ms=1_500,
                    frame=25,
                    out_time_us=1_000_000,
                    progress="continue",
                )
            )
            stopped = await state.recording_service.stop_recording("group-a")
            assert stopped.active is False
            assert stopped.session is not None
            assert stopped.session.status == "ended"
            assert stopped.session.end_reason == "explicit_stop"
            assert stopped.session.ffmpeg_returncode == 0
            assert stopped.session.stop_stage == "graceful"

            segments = await state.recording_service.list_segments(room_key="group-a")
            assert len(segments) == 1
            assert segments[0].relative_path.endswith("/media/00000.mkv")
            assert segments[0].size_bytes == 5
            assert segments[0].segment_end_seconds == 1.0

            recipient = await state.recipient_repository.get_state("group-a")
            assert recipient.session_id == session_id
            assert recipient.session_status == "ended"
            assert recipient.interval is None

            repeated = await state.recording_service.stop_recording("group-a")
            assert repeated.session is not None
            assert repeated.session.id == session_id
        finally:
            await state.stop()

    asyncio.run(scenario())


def test_recording_repository_recovers_prior_active_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = _settings(tmp_path)
        state = AppState.create(settings, live_page_client=FakeLivePageClient())  # type: ignore[arg-type]
        await state.database.initialize()
        await state.database.execute(
            "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, ?, ?)",
            (state.runtime_instance_id, "test", 1),
        )
        await state.room_repository.create_room(
            RoomCreate(room_key="group-a", room_url="73504089679")
        )
        await state.recipient_service.start_session(
            session_id="session-recovery",
            room_key="group-a",
            started_at_ms=10,
            started_monotonic_ns=10,
            runtime_instance_id=state.runtime_instance_id,
            recording_protocol="flv",
            recording_quality="origin",
            input_host="pull.example.douyincdn.com",
            input_path_sha256="a" * 64,
            input_url_sha256="b" * 64,
            input_query_keys_json="[]",
            recording_container="mkv",
            segment_seconds=600,
        )
        await state.database.execute(
            "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, ?, ?)",
            ("new-runtime", "test", 20),
        )
        repository = RecordingSessionRepository(state.database)
        recovered = await repository.recover_interrupted(
            runtime_instance_id="new-runtime",
            at_ms=20,
        )
        assert recovered == ["session-recovery"]
        session = await repository.get_session("session-recovery")
        assert session.status == "interrupted"
        assert session.end_reason == "app_restart_recovery"
        intervals = await state.recipient_repository.list_intervals(
            room_key="group-a",
            session_id="session-recovery",
        )
        assert intervals[-1].ended_at_ms == 20
        await state.database.close()

    asyncio.run(scenario())


async def _wait_until_finished(state: AppState, room_key: str) -> None:
    for _ in range(100):
        current = await state.recording_service.get_state(room_key)
        if not current.active:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("recording session did not finish")


def test_recording_natural_and_nonzero_exit_close_recipient_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        async def fake_check_tool(name: str, configured: str, *, timeout: float = 5.0):
            del timeout
            return ToolStatus(name, configured, configured, True, f"{name} test", "")

        monkeypatch.setattr("app.state.check_tool", fake_check_tool)
        factory = FakeSupervisorFactory()
        state = AppState.create(
            _settings(tmp_path),
            live_page_client=FakeLivePageClient(),  # type: ignore[arg-type]
            recording_supervisor_factory=factory,
        )
        await state.start()
        try:
            await state.room_service.create_room(
                RoomCreate(room_key="group-a", room_url="73504089679")
            )
            first = await state.recording_service.start_recording("group-a")
            assert first.session is not None
            factory.instances[-1].finish(returncode=0)
            await _wait_until_finished(state, "group-a")
            first_final = await state.recording_repository.get_session(first.session.id)
            assert first_final.status == "ended"
            assert first_final.end_reason == "natural_exit"
            assert first_final.ffmpeg_returncode == 0
            assert (await state.recipient_repository.get_state("group-a")).interval is None

            second = await state.recording_service.start_recording("group-a")
            assert second.session is not None
            factory.instances[-1].finish(returncode=9)
            await _wait_until_finished(state, "group-a")
            second_final = await state.recording_repository.get_session(second.session.id)
            assert second_final.status == "failed"
            assert second_final.end_reason == "ffmpeg_exit_nonzero"
            assert second_final.ffmpeg_returncode == 9
            assert second_final.recording_error_code == "ffmpeg_exit_nonzero"
            assert (await state.recipient_repository.get_state("group-a")).interval is None
        finally:
            await state.stop()

    asyncio.run(scenario())


def test_recording_prepare_failure_and_shutdown_are_persisted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FailingFactory:
        def __call__(self, spec, *, on_progress=None, on_stderr=None):
            del spec, on_progress, on_stderr
            raise RuntimeError("synthetic factory failure")

    async def scenario() -> None:
        async def fake_check_tool(name: str, configured: str, *, timeout: float = 5.0):
            del timeout
            return ToolStatus(name, configured, configured, True, f"{name} test", "")

        monkeypatch.setattr("app.state.check_tool", fake_check_tool)
        failed = AppState.create(
            _settings(tmp_path / "failed"),
            live_page_client=FakeLivePageClient(),  # type: ignore[arg-type]
            recording_supervisor_factory=FailingFactory(),
        )
        await failed.start()
        try:
            await failed.room_service.create_room(
                RoomCreate(room_key="group-a", room_url="73504089679")
            )
            try:
                await failed.recording_service.start_recording("group-a")
            except Exception as exc:
                assert "准备失败" in str(exc)
            else:
                raise AssertionError("prepare failure was not raised")
            sessions = await failed.recording_repository.list_sessions(room_key="group-a")
            assert len(sessions) == 1
            assert sessions[0].status == "failed"
            assert sessions[0].end_reason == "recorder_start_failed"
            assert sessions[0].recording_error_code == "prepare_runtimeerror"
            assert (await failed.recipient_repository.get_state("group-a")).interval is None
        finally:
            await failed.stop()

        factory = FakeSupervisorFactory()
        state = AppState.create(
            _settings(tmp_path / "shutdown"),
            live_page_client=FakeLivePageClient(),  # type: ignore[arg-type]
            recording_supervisor_factory=factory,
        )
        await state.start()
        try:
            await state.room_service.create_room(
                RoomCreate(room_key="group-b", room_url="73504089679")
            )
            started = await state.recording_service.start_recording("group-b")
            assert started.session is not None
            await state.recording_service.close()
            final = await state.recording_repository.get_session(started.session.id)
            assert final.status == "interrupted"
            assert final.end_reason == "app_shutdown"
            assert final.stop_stage == "graceful"
            assert (await state.recipient_repository.get_state("group-b")).interval is None
        finally:
            await state.stop()

    asyncio.run(scenario())

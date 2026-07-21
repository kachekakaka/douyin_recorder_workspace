from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.state as state_module
from app.main import create_app
from app.paths import ROOT, RuntimePaths
from app.runtime import ToolStatus
from app.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "config.json.default").write_text(
        json.dumps(
            {
                "server": {
                    "host": "127.0.0.1",
                    "port": 3399,
                    "auth_required": False,
                },
                "poll": {
                    "enabled": False,
                    "jitter_seconds": 0,
                    "offline_confirmations": 2,
                    "max_parallel_checks": 3,
                },
            }
        ),
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


def test_manager_and_worker_api_are_stable_and_same_origin_protected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def fake_check_tool(name: str, configured: str, *, timeout: float = 5.0):
        del timeout
        return ToolStatus(name, configured, configured, True, f"{name} test", "")

    monkeypatch.setattr(state_module, "check_tool", fake_check_tool)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:3399") as client:
        status = client.get("/api/manager/status")
        assert status.status_code == 200
        data = status.json()["data"]
        assert data == {
            "enabled": False,
            "running": False,
            "max_parallel_checks": 3,
            "worker_count": 0,
            "workers": [],
        }

        cross_origin = client.post(
            "/api/manager/actions/reconcile",
            headers={"Origin": "https://evil.example"},
        )
        assert cross_origin.status_code == 403

        reconciled = client.post("/api/manager/actions/reconcile")
        assert reconciled.status_code == 200
        assert reconciled.json()["data"]["running"] is False

        created = client.post(
            "/api/rooms",
            json={"room_key": "room-a", "room_url": "73504089679"},
        )
        assert created.status_code == 201

        worker = client.get("/api/rooms/room-a/worker")
        assert worker.status_code == 200
        worker_data = worker.json()["data"]
        assert worker_data["room_key"] == "room-a"
        assert worker_data["configured"] is True
        assert worker_data["running"] is False
        assert worker_data["lifecycle"] == "stopped"
        assert "http" not in json.dumps(worker_data).casefold()

        missing = client.get("/api/rooms/missing-room/worker")
        assert missing.status_code == 404


def test_enabled_manager_automatically_starts_and_stops_recording(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import asyncio
    import time

    from app.douyin.live_page import inspect_live_page
    from app.media import ProgressSnapshot, RecorderResult
    from app.state import AppState

    fixture = ROOT / "tests" / "fixtures" / "douyin" / "live-page.synthetic.html"

    class FakeLivePageClient:
        async def check(self, room_reference: str):
            return inspect_live_page(
                fixture.read_bytes(),
                room_url=room_reference,
                http_status=200,
                final_url=room_reference,
            )

        async def close(self) -> None:
            return None

    class FakeSupervisor:
        def __init__(self, spec, *, on_progress=None, on_stderr=None) -> None:
            del on_progress, on_stderr
            self.spec = spec
            self.running = False
            self.stop_calls = 0
            self.future: asyncio.Future[RecorderResult] = (
                asyncio.get_running_loop().create_future()
            )

        async def start(self) -> None:
            self.running = True

        async def wait(self) -> RecorderResult:
            return await self.future

        async def stop(
            self,
            *,
            graceful_timeout: float = 10.0,
            terminate_timeout: float = 5.0,
        ) -> RecorderResult:
            del graceful_timeout, terminate_timeout
            self.stop_calls += 1
            self.running = False
            if not self.future.done():
                self.future.set_result(
                    RecorderResult(
                        started_at_ms=1,
                        ended_at_ms=2,
                        returncode=0,
                        stop_stage="graceful",
                        last_progress=ProgressSnapshot(
                            received_at_ms=2,
                            frame=1,
                            out_time_us=100_000,
                            progress="end",
                        ),
                        stderr_lines=0,
                        callback_error_count=0,
                        redacted_argv=self.spec.redacted_argv,
                    )
                )
            return await self.future

    class Factory:
        def __init__(self) -> None:
            self.instances: list[FakeSupervisor] = []

        def __call__(self, spec, *, on_progress=None, on_stderr=None):
            value = FakeSupervisor(
                spec,
                on_progress=on_progress,
                on_stderr=on_stderr,
            )
            self.instances.append(value)
            return value

    async def fake_check_tool(name: str, configured: str, *, timeout: float = 5.0):
        del timeout
        return ToolStatus(name, configured, configured, True, f"{name} test", "")

    monkeypatch.setattr(state_module, "check_tool", fake_check_tool)
    config = tmp_path / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "config.json.default").write_text(
        json.dumps(
            {
                "server": {
                    "host": "127.0.0.1",
                    "port": 3399,
                    "auth_required": False,
                },
                "poll": {
                    "enabled": True,
                    "jitter_seconds": 0,
                    "offline_confirmations": 2,
                    "max_parallel_checks": 2,
                },
            }
        ),
        encoding="utf-8",
    )
    settings = Settings.load(
        root=tmp_path,
        paths=RuntimePaths.defaults(tmp_path),
        environ={
            "DOUYIN_RECORDER_PROTOCOL_CONTRACT": str(
                ROOT / "app" / "douyin" / "contracts" / "provisional_v1.json"
            )
        },
    )
    factory = Factory()
    state = AppState.create(
        settings,
        live_page_client=FakeLivePageClient(),  # type: ignore[arg-type]
        recording_supervisor_factory=factory,
    )
    app = create_app(state=state)

    with TestClient(app, base_url="http://127.0.0.1:3399") as client:
        created = client.post(
            "/api/rooms",
            json={
                "room_key": "room-auto",
                "room_url": "73504089679",
                "poll_interval_seconds": 5,
            },
        )
        assert created.status_code == 201

        recording = None
        for _ in range(100):
            recording = client.get("/api/rooms/room-auto/recording").json()["data"]
            if recording["active"] and factory.instances:
                break
            time.sleep(0.01)
        assert recording is not None and recording["active"] is True
        assert len(factory.instances) == 1

        worker = client.get("/api/rooms/room-auto/worker").json()["data"]
        assert worker["running"] is True
        assert worker["recording_active"] is True
        assert worker["last_live_state"] == "live"

        disabled = client.post("/api/rooms/room-auto/actions/disable")
        assert disabled.status_code == 200
        stopped = client.get("/api/rooms/room-auto/recording").json()["data"]
        assert stopped["active"] is False
        assert factory.instances[0].stop_calls >= 1
        worker = client.get("/api/rooms/room-auto/worker").json()["data"]
        assert worker["running"] is False
        assert worker["lifecycle"] == "disabled"

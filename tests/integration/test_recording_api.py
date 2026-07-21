from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import app.state as state_module
from app.douyin.live_page import inspect_live_page
from app.main import create_app
from app.media import ProgressSnapshot, RecorderResult
from app.paths import ROOT, RuntimePaths
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
        self.future: asyncio.Future[RecorderResult] = asyncio.get_running_loop().create_future()

    async def start(self) -> None:
        self.running = True

    async def wait(self) -> RecorderResult:
        return await self.future

    async def stop(self, *, graceful_timeout: float = 10.0, terminate_timeout: float = 5.0):
        del graceful_timeout, terminate_timeout
        if not self.future.done():
            self.running = False
            self.future.set_result(
                RecorderResult(
                    started_at_ms=1,
                    ended_at_ms=2,
                    returncode=0,
                    stop_stage="graceful",
                    last_progress=ProgressSnapshot(
                        received_at_ms=2,
                        frame=10,
                        out_time_us=500_000,
                        progress="end",
                    ),
                    stderr_lines=0,
                    callback_error_count=0,
                    redacted_argv=self.spec.redacted_argv,
                )
            )
        return await self.future


class FakeSupervisorFactory:
    def __init__(self) -> None:
        self.instances: list[FakeSupervisor] = []

    def __call__(self, spec, *, on_progress=None, on_stderr=None) -> FakeSupervisor:
        value = FakeSupervisor(spec, on_progress=on_progress, on_stderr=on_stderr)
        self.instances.append(value)
        return value


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


def test_recording_api_lifecycle_and_privacy(tmp_path: Path, monkeypatch) -> None:
    async def fake_check_tool(name: str, configured: str, *, timeout: float = 5.0):
        del timeout
        return ToolStatus(name, configured, configured, True, f"{name} test", "")

    monkeypatch.setattr(state_module, "check_tool", fake_check_tool)
    factory = FakeSupervisorFactory()
    state = AppState.create(
        _settings(tmp_path),
        live_page_client=FakeLivePageClient(),  # type: ignore[arg-type]
        recording_supervisor_factory=factory,
    )
    app = create_app(state=state)

    with TestClient(app, base_url="http://127.0.0.1:3399") as client:
        assert client.post(
            "/api/rooms",
            json={"room_key": "group-a", "room_url": "73504089679"},
        ).status_code == 201

        started = client.post("/api/rooms/group-a/actions/start-recording")
        assert started.status_code == 200
        start_data = started.json()["data"]
        assert start_data["active"] is True
        session_id = start_data["session"]["id"]
        rendered = json.dumps(start_data, sort_keys=True)
        assert "SECRET" not in rendered
        assert "signature=" not in rendered
        assert "https://" not in rendered

        duplicate = client.post("/api/rooms/group-a/actions/start-recording")
        assert duplicate.status_code == 409
        assert client.get("/api/rooms/group-a/recording").json()["data"]["active"] is True

        fake = factory.instances[0]
        media_dir = fake.spec.cwd / "media"
        (media_dir / "00000.mkv").write_bytes(b"segment")
        (media_dir / "segments.csv").write_text(
            "00000.mkv,0.0,1.25\n00001.mkv,1.25",
            encoding="utf-8",
        )

        stopped = client.post("/api/rooms/group-a/actions/stop-recording")
        assert stopped.status_code == 200
        assert stopped.json()["data"]["active"] is False

        sessions = client.get("/api/rooms/group-a/recording/sessions")
        assert sessions.status_code == 200
        assert sessions.json()["data"]["total"] == 1
        assert sessions.json()["data"]["items"][0]["id"] == session_id

        segments = client.get(
            "/api/rooms/group-a/recording/segments",
            params={"session_id": session_id},
        )
        assert segments.status_code == 200
        assert segments.json()["data"]["total"] == 1
        assert segments.json()["data"]["items"][0]["relative_path"].endswith(
            "/media/00000.mkv"
        )

        assert client.post("/api/rooms/group-a/actions/stop-recording").status_code == 200
        assert client.get("/api/rooms/missing/recording").status_code == 404
        assert client.get("/api/rooms/INVALID/recording").status_code == 422

        assert client.post("/api/rooms/group-a/actions/disable").status_code == 200
        disabled_start = client.post("/api/rooms/group-a/actions/start-recording")
        assert disabled_start.status_code == 409

    with sqlite3.connect(state.settings.paths.database_path) as connection:
        row = connection.execute(
            """
            SELECT input_host, input_path_sha256, input_url_sha256,
                   input_query_keys_json, last_progress_json
            FROM sessions WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        serialized = json.dumps(row)
        assert "SECRET" not in serialized
        assert "signature=" not in serialized
        assert "https://" not in serialized

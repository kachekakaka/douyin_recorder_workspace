from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.state as state_module
from app.db import Database
from app.main import create_app
from app.paths import ROOT, RuntimePaths
from app.runtime import ToolStatus
from app.settings import Settings

RECIPIENT_ID = "90071992547409931"


def _settings(tmp_path: Path) -> Settings:
    config = tmp_path / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "config.json.default").write_text(
        json.dumps(
            {
                "server": {"host": "127.0.0.1", "port": 3399, "auth_required": False},
                "jobs": {"enabled": False, "concurrency": 1, "max_attempts": 3},
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


async def _seed(settings: Settings) -> None:
    database = Database(settings.paths.database_path)
    await database.initialize()
    try:
        await database.execute(
            "INSERT INTO runtime_instances(id, app_version, started_at_ms) VALUES (?, ?, ?)",
            ("seed-runtime", "test", 1_000),
        )
        await database.execute(
            "INSERT INTO rooms(room_key, room_url, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, ?)",
            ("room-a", "https://live.douyin.com/79907888978", 1_000, 1_000),
        )
        await database.execute(
            """
            INSERT INTO sessions(
                id, room_key, status, started_at_ms, ended_at_ms, end_reason,
                runtime_instance_id, recording_protocol, recording_quality,
                recording_container, segment_seconds
            ) VALUES ('session-a', 'room-a', 'ended', 1000, 3000, 'test',
                      'seed-runtime', 'flv', 'origin', 'mkv', 10)
            """
        )
        await database.execute(
            """
            INSERT INTO media_files(
                id, session_id, relative_path, sequence, requested_start_at_ms,
                actual_start_at_ms, actual_end_at_ms, size_bytes, status,
                segment_start_seconds, segment_end_seconds, container, media_suffix
            ) VALUES ('media-a', 'session-a', 'room-a/session-a/media/00000.mkv', 0,
                      1000, 1000, 3000, 100, 'closed', 0, 2, 'mkv', '.mkv')
            """
        )
        await database.execute(
            """
            INSERT INTO recipient_intervals(
                id, session_id, status, reason, recipient_key,
                started_at_ms, ended_at_ms, started_monotonic_ns,
                ended_monotonic_ns, runtime_instance_id, ended_runtime_instance_id
            ) VALUES (1, 'session-a', 'active', NULL, ?, 1000, 3000,
                      1000000, 3000000, 'seed-runtime', 'seed-runtime')
            """,
            (f"uid:{RECIPIENT_ID}",),
        )
    finally:
        await database.close()


def test_postprocess_api_is_idempotent_private_and_same_origin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def fake_check_tool(name: str, configured: str, *, timeout: float = 5.0):
        del timeout
        return ToolStatus(name, configured, configured, True, f"{name} test", "")

    settings = _settings(tmp_path)
    asyncio.run(_seed(settings))
    monkeypatch.setattr(state_module, "check_tool", fake_check_tool)
    app = create_app(settings=settings)
    with TestClient(app, base_url="http://127.0.0.1:3399") as client:
        cross_origin = client.post(
            "/api/recording/sessions/session-a/actions/create-export",
            headers={"Origin": "https://evil.example"},
        )
        assert cross_origin.status_code == 403

        created = client.post(
            "/api/recording/sessions/session-a/actions/create-export"
        )
        assert created.status_code == 202
        job = created.json()["data"]
        rendered = json.dumps(job, sort_keys=True)
        assert job["status"] == "queued"
        assert RECIPIENT_ID not in rendered
        assert "uid:" not in rendered
        assert "raw_payload" not in rendered
        assert len(job["outputs"]) == 1
        assert job["outputs"][0]["recipient_key_sha256"]

        duplicate = client.post(
            "/api/recording/sessions/session-a/actions/create-export"
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["data"]["id"] == job["id"]

        listed = client.get("/api/jobs?status=queued")
        assert listed.status_code == 200
        assert listed.json()["data"]["total"] == 1
        assert listed.json()["data"]["items"][0]["id"] == job["id"]

        fetched = client.get(f"/api/jobs/{job['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["data"]["id"] == job["id"]

        canceled = client.post(f"/api/jobs/{job['id']}/actions/cancel")
        assert canceled.status_code == 200
        assert canceled.json()["data"]["status"] == "canceled"

        retried = client.post(f"/api/jobs/{job['id']}/actions/retry")
        assert retried.status_code == 200
        assert retried.json()["data"]["status"] == "queued"

        missing = client.get("/api/jobs/missing")
        assert missing.status_code == 404

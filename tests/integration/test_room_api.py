from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.state as state_module
from app.douyin.live_page import inspect_live_page
from app.main import create_app
from app.paths import ROOT, RuntimePaths
from app.runtime import ToolStatus
from app.settings import Settings
from app.state import AppState

FIXTURE = ROOT / "tests" / "fixtures" / "douyin" / "live-page.synthetic.html"


class FakeLivePageClient:
    closed = False

    async def check(self, room_reference: str):
        return inspect_live_page(
            FIXTURE.read_bytes(),
            room_url=room_reference,
            http_status=200,
            final_url="https://live.douyin.com/73504089679",
        )

    async def close(self) -> None:
        self.closed = True


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


def test_room_crud_and_sanitized_check_api(tmp_path: Path, monkeypatch) -> None:
    async def fake_check_tool(name: str, configured: str, *, timeout: float = 5.0):
        del timeout
        return ToolStatus(name, configured, configured, True, f"{name} test version", "")

    monkeypatch.setattr(state_module, "check_tool", fake_check_tool)
    fake_client = FakeLivePageClient()
    state = AppState.create(_settings(tmp_path), live_page_client=fake_client)  # type: ignore[arg-type]
    app = create_app(state=state)

    with TestClient(app) as client:
        created = client.post(
            "/api/rooms",
            json={"room_key": "group-a", "room_url": "73504089679"},
        )
        assert created.status_code == 201
        assert created.json()["data"]["room_url"] == "https://live.douyin.com/73504089679"

        duplicate = client.post(
            "/api/rooms",
            json={"room_key": "group-a", "room_url": "73504089679"},
        )
        assert duplicate.status_code == 409

        updated = client.patch(
            "/api/rooms/group-a",
            json={"quality": "hd", "protocol": "hls"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["quality"] == "hd"

        checked = client.post("/api/rooms/group-a/actions/check")
        assert checked.status_code == 200
        report = checked.json()["data"]
        assert report["live_state"] == "live"
        assert report["stream_candidate_count"] == 3
        rendered = json.dumps(report)
        assert "SECRET" not in rendered
        assert "PRIVATE" not in rendered

        listing = client.get("/api/rooms")
        assert listing.status_code == 200
        item = listing.json()["data"]["items"][0]
        assert item["latest_check"]["stream_candidate_count"] == 3

        status = client.get("/api/status")
        assert status.status_code == 200
        assert status.json()["data"]["phase"] == "P1A"
        assert status.json()["data"]["schema_version"] == 2

    assert fake_client.closed is True

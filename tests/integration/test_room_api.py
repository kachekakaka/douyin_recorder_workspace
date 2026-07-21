from __future__ import annotations

import json
import sqlite3
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

    with TestClient(app, base_url="http://127.0.0.1:3399") as client:
        rejected_cross_origin = client.post(
            "/api/rooms",
            headers={"Origin": "https://evil.example"},
            json={"room_key": "blocked", "room_url": "73504089679"},
        )
        assert rejected_cross_origin.status_code == 403
        assert rejected_cross_origin.json()["code"] == "cross_origin_write"

        rejected_cross_site_fetch = client.post(
            "/api/rooms",
            headers={"Sec-Fetch-Site": "cross-site"},
            json={"room_key": "blocked", "room_url": "73504089679"},
        )
        assert rejected_cross_site_fetch.status_code == 403
        assert rejected_cross_site_fetch.json()["code"] == "cross_origin_write"

        rejected_referer = client.post(
            "/api/rooms",
            headers={"Referer": "https://evil.example/page"},
            json={"room_key": "blocked", "room_url": "73504089679"},
        )
        assert rejected_referer.status_code == 403

        rejected_wrong_local_host = client.post(
            "/api/rooms",
            headers={"Host": "localhost:3399"},
            json={"room_key": "blocked", "room_url": "73504089679"},
        )
        assert rejected_wrong_local_host.status_code == 400
        assert rejected_wrong_local_host.json()["code"] == "invalid_host"

        created = client.post(
            "/api/rooms",
            headers={"Origin": "http://127.0.0.1:3399"},
            json={"room_key": "group-a", "room_url": "73504089679"},
        )
        assert created.status_code == 201
        assert created.json()["data"]["room_url"] == "https://live.douyin.com/73504089679"

        fetched = client.get("/api/rooms/group-a")
        assert fetched.status_code == 200
        assert fetched.json()["data"]["room_key"] == "group-a"
        assert isinstance(fetched.json()["data"]["created_at_ms"], int)

        missing = client.get("/api/rooms/missing-room")
        assert missing.status_code == 404

        invalid_key = client.get("/api/rooms/INVALID")
        assert invalid_key.status_code == 422

        duplicate = client.post(
            "/api/rooms",
            json={"room_key": "group-a", "room_url": "73504089679"},
        )
        assert duplicate.status_code == 409

        duplicate_url = client.post(
            "/api/rooms",
            json={"room_key": "group-b", "room_url": "73504089679"},
        )
        assert duplicate_url.status_code == 409

        second_room = client.post(
            "/api/rooms",
            json={"room_key": "group-b", "room_url": "73504089680"},
        )
        assert second_room.status_code == 201

        duplicate_url_update = client.patch(
            "/api/rooms/group-b",
            json={"room_url": "https://live.douyin.com/73504089679?from=test"},
        )
        assert duplicate_url_update.status_code == 409

        rejected_null = client.patch(
            "/api/rooms/group-a",
            json={"quality": None},
        )
        assert rejected_null.status_code == 422

        empty_patch = client.patch("/api/rooms/group-a", json={})
        assert empty_patch.status_code == 422

        unknown_patch = client.patch(
            "/api/rooms/group-a",
            json={"unknown": "value"},
        )
        assert unknown_patch.status_code == 422

        invalid_enum = client.patch(
            "/api/rooms/group-a",
            json={"protocol": "dash"},
        )
        assert invalid_enum.status_code == 422

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
        assert isinstance(report["external_room_id"], str)
        assert isinstance(report["web_rid"], str)
        rendered = json.dumps(report)
        assert "SECRET" not in rendered
        assert "PRIVATE" not in rendered

        listing = client.get("/api/rooms")
        assert listing.status_code == 200
        item = listing.json()["data"]["items"][0]
        assert item["latest_check"]["stream_candidate_count"] == 3

        disabled = client.post("/api/rooms/group-a/actions/disable")
        assert disabled.status_code == 200
        assert disabled.json()["data"]["enabled"] is False
        assert disabled.json()["data"]["latest_check"]["stream_candidate_count"] == 3

        enabled = client.post("/api/rooms/group-a/actions/enable")
        assert enabled.status_code == 200
        assert enabled.json()["data"]["enabled"] is True
        assert enabled.json()["data"]["latest_check"]["stream_candidate_count"] == 3

        status = client.get("/api/status")
        assert status.status_code == 200
        assert status.json()["data"]["phase"] == "P3A"
        assert status.json()["data"]["schema_version"] == 6

    with sqlite3.connect(state.settings.paths.database_path) as connection:
        detail_json = connection.execute(
            "SELECT detail_json FROM room_checks ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert "SECRET" not in detail_json
        assert "PRIVATE" not in detail_json
        assert "signature=" not in detail_json.casefold()

    assert fake_client.closed is True


def test_room_check_browser_fallback_stays_redacted_and_disable_clears_cache(
    tmp_path: Path, monkeypatch
) -> None:
    from app.douyin.live_page import stream_candidate_from_url
    from app.douyin.stream_resolver import BrowserObservation, DouyinStreamResolver

    async def fake_check_tool(name: str, configured: str, *, timeout: float = 5.0):
        del timeout
        return ToolStatus(name, configured, configured, True, f"{name} test version", "")

    class UnknownPageClient:
        closed = False

        async def check(self, room_reference: str):
            return inspect_live_page(
                b"<html><body>public room page</body></html>",
                room_url=room_reference,
                http_status=200,
                final_url=room_reference,
            )

        async def close(self) -> None:
            self.closed = True

    async def browser(room_url: str, duration: float) -> BrowserObservation:
        del duration
        candidate = stream_candidate_from_url(
            "https://pull.example.douyincdn.com/live/PATH-SECRET/live.flv"
            "?sign=QUERY-SECRET&expire=9",
            source_path="browser/network-response",
        )
        assert candidate is not None
        return BrowserObservation(
            candidates=(candidate,),
            page_loaded=True,
            page_http_status=200,
            final_host="live.douyin.com",
            final_path="/79907888978",
        )

    monkeypatch.setattr(state_module, "check_tool", fake_check_tool)
    page_client = UnknownPageClient()
    resolver = DouyinStreamResolver(page_client, browser_observer=browser)  # type: ignore[arg-type]
    state = AppState.create(_settings(tmp_path), stream_resolver=resolver)
    app = create_app(state=state)

    with TestClient(app, base_url="http://127.0.0.1:3399") as client:
        created = client.post(
            "/api/rooms",
            json={"room_key": "group-c", "room_url": "79907888978"},
        )
        assert created.status_code == 201
        checked = client.post("/api/rooms/group-c/actions/check")
        assert checked.status_code == 200
        report = checked.json()["data"]
        assert report["live_state"] == "live"
        assert report["stream_candidate_count"] == 1
        rendered = json.dumps(report, sort_keys=True)
        assert "PATH-SECRET" not in rendered
        assert "QUERY-SECRET" not in rendered
        assert "url" not in report["stream_candidates"][0]
        assert "path" not in report["stream_candidates"][0]
        assert resolver.cached_candidates("79907888978")
        disabled = client.post("/api/rooms/group-c/actions/disable")
        assert disabled.status_code == 200
        assert resolver.cached_candidates("79907888978") == ()

    with sqlite3.connect(state.settings.paths.database_path) as connection:
        detail_json = connection.execute(
            "SELECT detail_json FROM room_checks ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert "PATH-SECRET" not in detail_json
        assert "QUERY-SECRET" not in detail_json
        assert "https://pull" not in detail_json

    assert page_client.closed is True

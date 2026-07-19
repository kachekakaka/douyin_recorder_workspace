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


def test_fastapi_health_readiness_status_and_static_page(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def fake_check_tool(name: str, configured: str, *, timeout: float = 5.0):
        del timeout
        return ToolStatus(name, configured, configured, True, f"{name} test version", "")

    monkeypatch.setattr(state_module, "check_tool", fake_check_tool)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:3399") as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        assert health.headers["x-content-type-options"] == "nosniff"

        invalid_host = client.get("/healthz", headers={"Host": "evil.example"})
        assert invalid_host.status_code == 400
        assert invalid_host.json()["code"] == "invalid_host"
        assert invalid_host.headers["x-frame-options"] == "DENY"

        invalid_userinfo_host = client.get("/healthz", headers={"Host": "@127.0.0.1"})
        assert invalid_userinfo_host.status_code == 400
        assert invalid_userinfo_host.json()["code"] == "invalid_host"

        duplicate_host = client.get(
            "/healthz",
            headers=[("Host", "127.0.0.1:3399"), ("Host", "evil.example")],
        )
        assert duplicate_host.status_code == 400
        assert duplicate_host.json()["code"] == "invalid_host"

        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["data"]["schema_version"] == 2
        assert ready.json()["data"]["ffmpeg"]["ready"] is True

        status = client.get("/api/status")
        assert status.status_code == 200
        payload = status.json()["data"]
        assert payload["phase"] == "P1A"
        assert payload["loopback_only"] is True
        assert payload["protocol_live_verified"] is False

        page = client.get("/")
        assert page.status_code == 200
        assert "当前推荐收礼人" in page.text

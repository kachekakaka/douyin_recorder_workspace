from __future__ import annotations

import asyncio

import httpx
import pytest

from app.douyin.live_page import (
    MAX_REDIRECTS,
    DouyinLivePageClient,
    LivePageError,
    inspect_live_page,
    normalize_room_reference,
)
from app.paths import ROOT

FIXTURE = ROOT / "tests" / "fixtures" / "douyin" / "live-page.synthetic.html"
ROOM_ID = "73504089679"
ROOM_URL = f"https://live.douyin.com/{ROOM_ID}"


def _inspect(body: bytes, *, status: int = 200):
    return inspect_live_page(body, room_url=ROOM_ID, http_status=status, final_url=ROOM_URL)


def test_room_reference_normalization_is_strict() -> None:
    by_id = normalize_room_reference(ROOM_ID)
    assert by_id.room_url == ROOM_URL
    assert normalize_room_reference(f"{ROOM_URL}?from=chat#x") == by_id

    unsafe = (
        f"http://live.douyin.com/{ROOM_ID}",
        f"https://127.0.0.1/{ROOM_ID}",
        "https://live.douyin.com/a/b",
        f"https://user:pass@live.douyin.com/{ROOM_ID}",
        f"https://@live.douyin.com/{ROOM_ID}",
        f"https://live.douyin.com:99999/{ROOM_ID}",
    )
    for value in unsafe:
        with pytest.raises(LivePageError):
            normalize_room_reference(value)


def test_fixture_extracts_flv_hls_and_quality_without_public_secret_leakage() -> None:
    result = _inspect(FIXTURE.read_bytes())
    assert result.snapshot.live_state == "live"
    assert result.snapshot.external_room_id == "998877665544332211"
    assert result.snapshot.web_rid == ROOM_ID
    assert {(item.protocol, item.quality) for item in result.candidates} == {
        ("flv", "origin"),
        ("flv", "hd"),
        ("hls", "origin"),
    }
    assert all(
        forbidden not in item.url
        for item in result.candidates
        for forbidden in ("attacker.invalid", "127.0.0.1", "localhost", ".local", "8.8.8.8")
    )

    public = result.snapshot.to_public_dict()
    rendered = repr(public) + repr(result) + repr(result.snapshot) + repr(result.candidates[0])
    for secret in (
        "SECRET-FLV",
        "SECRET-HLS",
        "SECRET-HD",
        "PATH-TOKEN-PRIVATE",
        "PRIVATE-IP",
        "PRIVATE-LOCALHOST",
        "PRIVATE-LOCAL",
        "THIRD-PARTY",
        "PUBLIC-IP-LITERAL",
        "PRIVATE-WSS",
    ):
        assert secret not in rendered
    assert public["sanitized_websocket_endpoints"] == (
        "wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/",
    )
    candidates = public["stream_candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 3
    assert all("url" not in item and "path" not in item for item in candidates)
    assert {item["path_suffix"] for item in candidates} == {".flv", ".m3u8"}
    assert all(
        len(item["path_sha256"]) == 64 and len(item["url_sha256"]) == 64
        for item in candidates
    )
    assert {tuple(item["query_keys"]) for item in candidates} == {
        ("expire", "signature"),
        ("session", "token"),
        ("sign",),
    }


def test_only_allowlisted_default_port_stream_hosts_are_candidates() -> None:
    body = b'''<script type="application/json">{
      "stream_url": {
        "flv": "http://127.0.0.1/private.flv",
        "hls": "https://attacker.invalid/live.m3u8?token=SECRET",
        "backup": "http://8.8.8.8/public.flv?token=SECRET"
      }
    }</script>'''
    result = _inspect(body)
    assert result.snapshot.live_state == "unknown"
    assert result.candidates == ()

    body = b'''<script type="application/json">{
      "SECRET_STREAM_TOKEN": "https://pull.example.douyincdn.com/live/ok.flv?token=VALUE",
      "invalid_port": "https://pull.example.douyincdn.com:99999/live/bad.flv",
      "wrong_https_port": "https://pull.example.douyincdn.com:80/live/bad.flv",
      "wrong_http_port": "http://pull.example.douyincdn.com:443/live/bad.flv",
      "userinfo": "https://@pull.example.douyincdn.com/live/bad.flv"
    }</script>'''
    result = _inspect(body)
    assert len(result.candidates) == 1
    candidate = result.snapshot.to_public_dict()["stream_candidates"][0]
    assert candidate["source_path"].startswith("<key-")
    assert "SECRET_STREAM_TOKEN" not in repr(candidate)


def _run_redirect(location: str) -> tuple[str, str | None]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": location})

    async def scenario() -> tuple[str, str | None]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await DouyinLivePageClient(client=client).check(ROOM_ID)
            return result.snapshot.live_state, result.snapshot.error_code

    return asyncio.run(scenario())


def test_client_rejects_redirects_outside_allowlist_or_with_credentials_or_ports() -> None:
    for location in (
        "https://example.com/evil",
        f"https://user:pass@live.douyin.com/{ROOM_ID}",
        f"https://@live.douyin.com/{ROOM_ID}",
        f"https://live.douyin.com:99999/{ROOM_ID}",
        f"https://live.douyin.com:444/{ROOM_ID}",
        "https://[broken",
    ):
        assert _run_redirect(location) == ("error", "LivePageError")


def test_client_enforces_redirect_limit() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": f"/redirect-{len(requested)}"})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await DouyinLivePageClient(client=client).check(ROOM_ID)
        assert result.snapshot.live_state == "error"
        assert result.snapshot.error_code == "LivePageError"
        assert len(requested) == MAX_REDIRECTS + 1

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [304, 404, 500])
def test_non_success_status_cannot_be_misclassified_live(status: int) -> None:
    body = b'<script type="application/json">{"flv":"https://pull.example.douyincdn.com/live/error.flv?token=SECRET"}</script>'
    result = _inspect(body, status=status)
    assert result.snapshot.live_state == "error"
    assert result.snapshot.error_code == f"http_{status}"
    assert result.candidates == ()
    assert result.snapshot.stream_candidates == ()


def test_blocked_unknown_and_network_error_are_distinct() -> None:
    blocked = _inspect("请完成下列验证后继续访问".encode())
    assert blocked.snapshot.live_state == "blocked"

    unknown = _inspect(b"<html><body>ordinary page</body></html>")
    assert unknown.snapshot.live_state == "unknown"
    assert unknown.snapshot.error_code is None

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic network failure", request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            failed = await DouyinLivePageClient(client=client).check(ROOM_ID)
        assert failed.snapshot.live_state == "error"
        assert failed.snapshot.error_code == "ConnectError"
        assert failed.candidates == ()

    asyncio.run(scenario())

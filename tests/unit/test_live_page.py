from __future__ import annotations

import pytest

from app.douyin.live_page import (
    LivePageError,
    inspect_live_page,
    normalize_room_reference,
)
from app.paths import ROOT

FIXTURE = ROOT / "tests" / "fixtures" / "douyin" / "live-page.synthetic.html"


def test_room_reference_normalization_is_strict() -> None:
    by_id = normalize_room_reference("73504089679")
    assert by_id.room_url == "https://live.douyin.com/73504089679"
    by_url = normalize_room_reference("https://live.douyin.com/73504089679?from=chat#x")
    assert by_url == by_id

    for unsafe in (
        "http://live.douyin.com/73504089679",
        "https://127.0.0.1/73504089679",
        "https://live.douyin.com/a/b",
        "https://user:pass@live.douyin.com/73504089679",
        "https://@live.douyin.com/73504089679",
        "https://live.douyin.com:99999/73504089679",
    ):
        with pytest.raises(LivePageError):
            normalize_room_reference(unsafe)


def test_live_page_fixture_extracts_private_candidates_but_public_result_is_redacted() -> None:
    result = inspect_live_page(
        FIXTURE.read_bytes(),
        room_url="73504089679",
        http_status=200,
        final_url="https://live.douyin.com/73504089679",
    )

    assert result.snapshot.live_state == "live"
    assert result.snapshot.external_room_id == "998877665544332211"
    assert result.snapshot.web_rid == "73504089679"
    assert result.snapshot.title == "Synthetic Group Live"
    assert len(result.candidates) == 3
    assert {(item.protocol, item.quality) for item in result.candidates} == {
        ("flv", "origin"),
        ("flv", "hd"),
        ("hls", "origin"),
    }
    assert any("SECRET-FLV" in item.url for item in result.candidates)
    assert all("attacker.invalid" not in item.url for item in result.candidates)
    assert all("127.0.0.1" not in item.url for item in result.candidates)
    assert all("localhost" not in item.url for item in result.candidates)

    public = result.snapshot.to_public_dict()
    rendered = str(public)
    private_values = (
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
    )
    assert all(value not in rendered for value in private_values)
    assert all(value not in repr(result) for value in private_values)
    assert all(value not in repr(result.snapshot) for value in private_values)
    assert all(value not in repr(result.candidates[0]) for value in private_values)
    assert public["stream_candidate_count"] == 3
    assert public["sanitized_websocket_endpoints"] == (
        "wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/",
    )
    candidates = public["stream_candidates"]
    assert isinstance(candidates, list)
    assert all("url" not in item for item in candidates)
    assert all("path" not in item for item in candidates)
    assert {item["path_suffix"] for item in candidates} == {".flv", ".m3u8"}
    assert all(len(item["path_sha256"]) == 64 for item in candidates)
    assert all(len(item["url_sha256"]) == 64 for item in candidates)
    assert {tuple(item["query_keys"]) for item in candidates} == {
        ("expire", "signature"),
        ("session", "token"),
        ("sign",),
    }


def test_live_page_rejects_private_or_unknown_stream_hosts() -> None:
    body = b"""<script type="application/json">{
      "stream_url": {
        "flv": "http://127.0.0.1/private.flv",
        "hls": "https://attacker.invalid/live.m3u8?token=SECRET",
        "backup": "http://8.8.8.8/public.flv?token=SECRET"
      }
    }</script>"""
    result = inspect_live_page(
        body,
        room_url="73504089679",
        http_status=200,
        final_url="https://live.douyin.com/73504089679",
    )
    assert result.snapshot.live_state == "unknown"
    assert result.candidates == ()


def test_live_page_rejects_invalid_ports_and_redacts_untrusted_source_keys() -> None:
    body = b"""<script type="application/json">{
      "SECRET_STREAM_TOKEN": "https://pull.example.douyincdn.com/live/ok.flv?token=VALUE",
      "invalid_port": "https://pull.example.douyincdn.com:99999/live/bad.flv",
      "wrong_https_port": "https://pull.example.douyincdn.com:80/live/bad.flv",
      "wrong_http_port": "http://pull.example.douyincdn.com:443/live/bad.flv",
      "empty_userinfo": "https://@pull.example.douyincdn.com/live/bad.flv"
    }</script>"""
    result = inspect_live_page(
        body,
        room_url="73504089679",
        http_status=200,
        final_url="https://live.douyin.com/73504089679",
    )

    assert len(result.candidates) == 1
    public_candidates = result.snapshot.to_public_dict()["stream_candidates"]
    assert isinstance(public_candidates, list)
    assert "SECRET_STREAM_TOKEN" not in str(public_candidates)
    assert public_candidates[0]["source_path"].startswith("<key-")


def test_live_page_client_rejects_redirect_outside_douyin() -> None:
    import asyncio

    import httpx

    from app.douyin.live_page import DouyinLivePageClient

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.com/evil"})

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            live_client = DouyinLivePageClient(client=client)
            result = await live_client.check("73504089679")
            assert result.snapshot.live_state == "error"
            assert result.snapshot.error_code == "LivePageError"
            assert requested == ["https://live.douyin.com/73504089679"]

    asyncio.run(scenario())


def test_live_page_non_success_status_is_error_and_has_no_usable_candidates() -> None:
    body = (
        b'<script type="application/json">'
        b'{"flv":"https://pull.example.douyincdn.com/live/error.flv?token=SECRET"}'
        b"</script>"
    )
    for status in (304, 404, 500):
        result = inspect_live_page(
            body,
            room_url="73504089679",
            http_status=status,
            final_url="https://live.douyin.com/73504089679",
        )
        assert result.snapshot.live_state == "error"
        assert result.snapshot.error_code == f"http_{status}"
        assert result.candidates == ()
        assert result.snapshot.stream_candidates == ()


def test_live_page_client_rejects_redirect_credentials_or_custom_port() -> None:
    import asyncio

    import httpx

    from app.douyin.live_page import DouyinLivePageClient

    async def scenario() -> None:
        for location in (
            "https://user:pass@live.douyin.com/73504089679",
            "https://@live.douyin.com/73504089679",
            "https://live.douyin.com:99999/73504089679",
            "https://live.douyin.com:444/73504089679",
            "https://[broken",
        ):

            def handler(_request: httpx.Request, target: str = location) -> httpx.Response:
                return httpx.Response(302, headers={"location": target})

            transport = httpx.MockTransport(handler)
            async with httpsyncClient(transport=transport, follow_redirects=False) as client:
                live_client = DouyinLivePageClient(client=client)
                result = await live_client.check("73504089679")
                assert result.snapshot.live_state == "error"
                assert result.snapshot.error_code == "LivePageError"

    asyncio.run(scenario())


def test_live_page_client_enforces_redirect_limit() -> None:
    import asyncio

    import httpx

    from app.douyin.live_page import MAX_REDIRECTS, DouyinLivePageClient

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        step = len(requested)
        return httpx.Response(302, headers={"location": f"/redirect-{step}"})

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await DouyinLivePageClient(client=client).check("73504089679")
            assert result.snapshot.live_state == "error"
            assert result.snapshot.error_code == "LivePageError"
            assert len(requested) == MAX_REDIRECTS + 1

    asyncio.run(scenario())


def test_live_page_distinguishes_blocked_unknown_and_network_error() -> None:
    import asyncio

    import httpx

    from app.douyin.live_page import DouyinLivePageClient

    blocked = inspect_live_page(
        "请完成下列隬见后继访闭".encode(),
        room_url="73504089679",
        http_status=200,
        final_url="https://live.douyin.com/73504089679",
    )
    assert blocked.snapshot.live_state == "blocked"
    assert blocked.candidates == ()

    unknown = inspect_live_page(
        b"<html><body>ordinary page without structured live data</body></html>",
        room_url="73504089679",
        http_status=200,
        final_url="https://live.douyin.com/73504089679",
    )
    assert unknown.snapshot.live_state == "unknown"
    assert unknown.snapshot.error_code is None

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic network failure", request=request)

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            failed = await DouyinLivePageClient(client=client).check("73504089679")
            assert failed.snapshot.live_state == "error"
            assert failed.snapshot.error_code == "ConnectError"
            assert failed.candidates == ()

    asyncio.run(scenario())

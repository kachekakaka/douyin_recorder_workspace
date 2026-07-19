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

    public = result.snapshot.to_public_dict()
    rendered = str(public)
    assert "SECRET-FLV" not in rendered
    assert "SECRET-HLS" not in rendered
    assert "PRIVATE" not in rendered
    assert public["stream_candidate_count"] == 3
    assert public["sanitized_websocket_endpoints"] == (
        "wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/",
    )
    candidates = public["stream_candidates"]
    assert isinstance(candidates, list)
    assert all("url" not in item for item in candidates)


def test_live_page_rejects_private_or_unknown_stream_hosts() -> None:
    body = b'''<script type="application/json">{
      "stream_url": {
        "flv": "http://127.0.0.1/private.flv",
        "hls": "https://attacker.invalid/live.m3u8?token=SECRET",
        "backup": "http://8.8.8.8/public.flv?token=SECRET"
      }
    }</script>'''
    result = inspect_live_page(
        body,
        room_url="73504089679",
        http_status=200,
        final_url="https://live.douyin.com/73504089679",
    )
    assert result.snapshot.live_state == "unknown"
    assert result.candidates == ()


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
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            live_client = DouyinLivePageClient(client=client)
            result = await live_client.check("73504089679")
            assert result.snapshot.live_state == "error"
            assert result.snapshot.error_code == "LivePageError"
            assert requested == ["https://live.douyin.com/73504089679"]

    asyncio.run(scenario())

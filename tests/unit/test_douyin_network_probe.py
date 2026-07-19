from __future__ import annotations

from tools.douyin_network_probe import (
    _decode_cdp_response_body,
    _public_candidate_from_network_url,
    _public_candidates_from_payload,
    _safe_room_api_endpoint,
)

ROOM_URL = "https://live.douyin.com/79907888978"


def test_room_api_endpoint_allowlist_ignores_query_values() -> None:
    assert _safe_room_api_endpoint(
        "https://live.douyin.com/webcast/room/web/enter/?web_rid=ROOM&msToken=SECRET"
    ) == ("web-enter", "live.douyin.com", "/webcast/room/web/enter")
    assert _safe_room_api_endpoint(
        "https://webcast.amemv.com/webcast/room/reflow/info/?a_bogus=SECRET"
    ) == ("h5-reflow", "webcast.amemv.com", "/webcast/room/reflow/info")
    for unsafe in (
        "http://live.douyin.com/webcast/room/web/enter/",
        "https://user:pass@live.douyin.com/webcast/room/web/enter/",
        "https://live.douyin.com:444/webcast/room/web/enter/",
        "https://attacker.invalid/webcast/room/web/enter/",
        "https://live.douyin.com/webcast/room/other/",
    ):
        assert _safe_room_api_endpoint(unsafe) is None


def test_cdp_body_and_candidate_reports_never_expose_url_values() -> None:
    assert _decode_cdp_response_body({"body": "{}", "base64Encoded": False}) == b"{}"
    assert _decode_cdp_response_body({"body": "e30=", "base64Encoded": True}) == b"{}"
    assert _decode_cdp_response_body({"body": "%%%", "base64Encoded": True}) is None

    candidate = _public_candidate_from_network_url(
        "https://pull.example.douyincdn.com/live/PATH-SECRET/room.flv?sign=QUERY-SECRET",
        room_url=ROOM_URL,
    )
    assert candidate is not None
    rendered = repr(candidate)
    assert "url" not in candidate and "path" not in candidate
    assert "PATH-SECRET" not in rendered
    assert "QUERY-SECRET" not in rendered
    assert candidate["query_keys"] == ["sign"]

    payload = b'''{"data":{"data":[{"stream_url":{"live_core_sdk_data":{"pull_data":{"stream_data":"{\\"data\\":{\\"origin\\":{\\"main\\":{\\"flv\\":\\"https://pull.example.douyincdn.com/live/API-PATH/live.flv?token=API-SECRET\\",\\"hls\\":\\"https://pull.example.douyincdn.com/live/API-PATH/live.m3u8?token=API-SECRET\\"}}}}"}}}}]}}'''
    candidates = _public_candidates_from_payload(payload, room_url=ROOM_URL)
    assert {(item["protocol"], item["quality"]) for item in candidates} == {
        ("flv", "origin"),
        ("hls", "origin"),
    }
    assert "API-PATH" not in repr(candidates)
    assert "API-SECRET" not in repr(candidates)

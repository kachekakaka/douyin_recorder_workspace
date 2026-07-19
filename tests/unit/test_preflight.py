from __future__ import annotations

import httpx

from tools.douyin_room_preflight import inspect_response


def test_preflight_report_strips_wss_query_and_does_not_store_html() -> None:
    body = (
        b'<html><script id="RENDER_DATA" type="application/json">'
        b'%7B%22roomId%22%3A%2273504089679%22%2C'
        b'%22web_rid%22%3A%2273504089679%22%2C%22status%22%3A2%2C'
        b'%22socket%22%3A%22wss%3A%2F%2Fwebcast5-ws-web-lf.douyin.com'
        b'%2Fwebcast%2Fim%2Fpush%2Fv2%2F%3Fsignature%3DSECRET'
        b'%26internal_ext%3DPRIVATE%22%7D</script></html>'
    )
    request = httpx.Request("GET", "https://live.douyin.com/73504089679")
    response = httpx.Response(200, content=body, request=request)

    report = inspect_response(response, requested_room_id="73504089679")

    assert "73504089679" in report["room_ids"]
    assert report["status_values"] == ["2"]
    endpoints = report["sanitized_websocket_endpoints"]
    assert endpoints == ["wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/"]
    rendered = str(report)
    assert "SECRET" not in rendered
    assert "PRIVATE" not in rendered
    assert "RENDER_DATA" not in rendered

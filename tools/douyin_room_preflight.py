from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

_MAX_BODY_BYTES = 4 * 1024 * 1024
_ROOM_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
_RENDER_DATA_RE = re.compile(
    r'<script[^>]+id=["\']RENDER_DATA["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_WSS_RE = re.compile(r"wss://[^\s\"'<>]+", re.IGNORECASE)
_ROOM_ID_PATTERNS = (
    re.compile(r'"roomId"\s*:\s*"?(\d{4,30})"?'),
    re.compile(r'"room_id"\s*:\s*"?(\d{4,30})"?'),
    re.compile(r'"room_id_str"\s*:\s*"(\d{4,30})"'),
)
_WEB_RID_PATTERNS = (
    re.compile(r'"web_rid"\s*:\s*"([A-Za-z0-9_.-]{3,80})"'),
    re.compile(r'"webRid"\s*:\s*"([A-Za-z0-9_.-]{3,80})"'),
)
_STATUS_PATTERNS = (
    re.compile(r'"status"\s*:\s*"?(\d{1,3})"?'),
    re.compile(r'"live_status"\s*:\s*"?(\d{1,3})"?'),
)
_SAFE_MARKERS = (
    "live_core_sdk_data",
    "stream_data",
    "flv_pull_url",
    "hls_pull_url",
    "web_rid",
    "roomId",
    "room_id",
    "webcast",
)
_BLOCK_MARKERS = (
    "captcha",
    "verifycenter",
    "security verification",
    "访问频繁",
    "验证后继续",
    "请完成下列验证",
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _unique_matches(patterns: tuple[re.Pattern[str], ...], text: str, limit: int = 20) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for value in pattern.findall(text):
            if value not in seen:
                seen.add(value)
                values.append(value)
            if len(values) >= limit:
                return values
    return values


def _sanitize_wss_url(value: str) -> str | None:
    try:
        parsed = urlsplit(html.unescape(value))
    except ValueError:
        return None
    if parsed.scheme.lower() != "wss" or not parsed.hostname:
        return None
    host = parsed.hostname.casefold().rstrip(".")
    if not (host == "douyin.com" or host.endswith(".douyin.com")):
        return None
    path = parsed.path or "/"
    return f"wss://{host}{path}"


def _decoded_views(body: str) -> list[str]:
    views = [body]
    for match in _RENDER_DATA_RE.findall(body):
        candidate = html.unescape(match.strip())
        views.append(candidate)
        with suppress(Exception):
            views.append(unquote(candidate))
    return views


def inspect_response(response: httpx.Response, *, requested_room_id: str) -> dict[str, object]:
    raw = response.content[:_MAX_BODY_BYTES]
    text = raw.decode(response.encoding or "utf-8", errors="replace")
    views = _decoded_views(text)
    joined = "\n".join(views)
    lower = joined.casefold()

    websocket_endpoints: list[str] = []
    for value in _WSS_RE.findall(joined):
        safe = _sanitize_wss_url(value)
        if safe and safe not in websocket_endpoints:
            websocket_endpoints.append(safe)
        if len(websocket_endpoints) >= 20:
            break

    final = urlsplit(str(response.url))
    blocked_markers = [marker for marker in _BLOCK_MARKERS if marker.casefold() in lower]
    marker_counts = {marker: lower.count(marker.casefold()) for marker in _SAFE_MARKERS}
    room_ids = _unique_matches(_ROOM_ID_PATTERNS, joined)
    web_rids = _unique_matches(_WEB_RID_PATTERNS, joined)
    status_values = _unique_matches(_STATUS_PATTERNS, joined)

    return {
        "schema_version": 1,
        "checked_at": _utc_now(),
        "requested_room_id": requested_room_id,
        "request_url": f"https://live.douyin.com/{requested_room_id}",
        "status_code": response.status_code,
        "redirect_count": len(response.history),
        "final_host": (final.hostname or "").casefold(),
        "final_path": final.path or "/",
        "content_type": response.headers.get("content-type", "").split(";", 1)[0],
        "body_bytes_read": len(raw),
        "body_truncated": len(response.content) > _MAX_BODY_BYTES,
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "blocked_markers": blocked_markers,
        "marker_counts": marker_counts,
        "room_ids": room_ids,
        "web_rids": web_rids,
        "status_values": status_values,
        "sanitized_websocket_endpoints": websocket_endpoints,
        "notes": [
            "报告不保存网页正文、Cookie、签名参数或完整 WSS query。",
            "status/marker 只能作为现场预检线索，不能替代目标消息解码验证。",
        ],
    }


def run_preflight(room_id: str, *, timeout_seconds: float = 25.0) -> dict[str, object]:
    if not _ROOM_ID_RE.fullmatch(room_id):
        raise ValueError("room_id 只允许 3–64 位字母、数字、点、下划线或横线")
    url = f"https://live.douyin.com/{room_id}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        "Cache-Control": "no-cache",
    }
    try:
        with httpx.Client(
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout_seconds),
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        return {
            "schema_version": 1,
            "checked_at": _utc_now(),
            "requested_room_id": room_id,
            "request_url": url,
            "request_error": f"{type(exc).__name__}: {exc}",
            "notes": ["网络失败不代表房间离线；需要在可访问抖音的环境重试。"],
        }
    return inspect_response(response, requested_room_id=room_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="抖音直播页安全预检（不保存正文或凭据）")
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=25.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run_preflight(args.room_id.strip(), timeout_seconds=args.timeout)
    except ValueError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

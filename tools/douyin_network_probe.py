from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import signal
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import websockets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from app.douyin.live_page import inspect_live_page  # noqa: E402
from tools.douyin_browser_probe import (  # noqa: E402
    CdpClient,
    _chrome_executable,
    _utc_now,
    _wait_for_page_target,
)

_MAX_CAPTURE_BODY_BYTES = 4 * 1024 * 1024
_ROOM_API_ENDPOINTS = {
    ("live.douyin.com", "/webcast/room/web/enter"): "web-enter",
    ("webcast.amemv.com", "/webcast/room/reflow/info"): "h5-reflow",
}


def _safe_https_endpoint(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (ValueError, UnicodeError):
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        return None
    path = (parsed.path or "/").rstrip("/") or "/"
    return host, path


def _safe_room_api_endpoint(value: str) -> tuple[str, str, str] | None:
    endpoint = _safe_https_endpoint(value)
    if endpoint is None:
        return None
    kind = _ROOM_API_ENDPOINTS.get(endpoint)
    if kind is None:
        return None
    return kind, endpoint[0], endpoint[1]


def _decode_cdp_response_body(value: dict[str, Any]) -> bytes | None:
    body = value.get("body")
    if not isinstance(body, str):
        return None
    try:
        raw = (
            base64.b64decode(body, validate=True)
            if value.get("base64Encoded")
            else body.encode("utf-8")
        )
    except (ValueError, TypeError, UnicodeError):
        return None
    return raw if len(raw) <= _MAX_CAPTURE_BODY_BYTES else None


def _public_candidates_from_payload(body: bytes, *, room_url: str) -> list[dict[str, object]]:
    result = inspect_live_page(
        body,
        room_url=room_url,
        http_status=200,
        final_url=room_url,
    )
    return [candidate.to_public_dict() for candidate in result.candidates]


def _public_candidate_from_network_url(
    value: str, *, room_url: str
) -> dict[str, object] | None:
    payload = json.dumps(
        {"stream_url": {"browser_network_request": value}},
        separators=(",", ":"),
    ).encode("utf-8")
    candidates = _public_candidates_from_payload(payload, room_url=room_url)
    return candidates[0] if candidates else None


def _merge_candidate(
    output: dict[str, dict[str, object]], candidate: dict[str, object]
) -> None:
    fingerprint = candidate.get("url_sha256")
    if isinstance(fingerprint, str) and len(fingerprint) == 64:
        output.setdefault(fingerprint, candidate)


async def run_network_probe(
    *,
    room_id: str,
    output: Path,
    duration_seconds: float,
    chrome: str,
    debug_port: int,
) -> dict[str, object]:
    allowed_room_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    if not room_id or any(char not in allowed_room_chars for char in room_id):
        raise ValueError("room_id is invalid")
    if not 5 <= duration_seconds <= 300:
        raise ValueError("duration must be between 5 and 300 seconds")

    room_url = f"https://live.douyin.com/{room_id}"
    started_at = _utc_now()
    chrome_executable = _chrome_executable(chrome)
    if chrome_executable is None:
        report = {
            "schema_version": 1,
            "room_id": room_id,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "status": "chrome-not-found",
            "notes": ["Chrome was not found; no network observation was performed."],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report

    user_data = tempfile.TemporaryDirectory(prefix="douyin-network-probe-")
    command = [
        chrome_executable,
        "--headless=new",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={user_data.name}",
        "about:blank",
    ]
    creationflags = 0
    start_new_session = os.name != "nt"
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=start_new_session,
        creationflags=creationflags,
    )

    page_loaded = False
    errors: Counter[str] = Counter()
    pending_api: dict[str, tuple[str, str, str, int]] = {}
    api_endpoint_counts: Counter[str] = Counter()
    api_status_counts: Counter[str] = Counter()
    api_state_counts: Counter[str] = Counter()
    api_candidates: dict[str, dict[str, object]] = {}
    media_candidates: dict[str, dict[str, object]] = {}
    cdp: CdpClient | None = None

    try:
        debugger_url = await _wait_for_page_target(debug_port)
        async with websockets.connect(debugger_url, max_size=8 * 1024 * 1024) as websocket:
            cdp = CdpClient(websocket)
            await cdp.command(
                "Network.enable",
                {"maxTotalBufferSize": 0, "maxResourceBufferSize": 0},
            )
            await cdp.command("Page.enable")
            await cdp.command("Page.navigate", {"url": room_url})
            deadline = time.monotonic() + duration_seconds
            while time.monotonic() < deadline:
                timeout = min(1.0, max(0.05, deadline - time.monotonic()))
                try:
                    event = await asyncio.wait_for(cdp.events.get(), timeout=timeout)
                except TimeoutError:
                    continue
                method = event.get("method")
                params = event.get("params")
                if not isinstance(params, dict):
                    continue
                if method == "Page.loadEventFired":
                    page_loaded = True
                    continue
                if method == "Network.responseReceived":
                    request_id = params.get("requestId")
                    response = params.get("response")
                    if not isinstance(request_id, str) or not isinstance(response, dict):
                        continue
                    url = response.get("url")
                    status = response.get("status")
                    if not isinstance(url, str):
                        continue
                    media = _public_candidate_from_network_url(url, room_url=room_url)
                    if media is not None:
                        _merge_candidate(media_candidates, media)
                    endpoint = _safe_room_api_endpoint(url)
                    if endpoint is not None and isinstance(status, (int, float)):
                        pending_api[request_id] = (*endpoint, int(status))
                        api_endpoint_counts[endpoint[0]] += 1
                        api_status_counts[str(int(status))] += 1
                    continue
                if method == "Network.loadingFailed":
                    request_id = params.get("requestId")
                    if isinstance(request_id, str) and pending_api.pop(request_id, None):
                        errors["room-api-loading-failed"] += 1
                    continue
                if method != "Network.loadingFinished":
                    continue
                request_id = params.get("requestId")
                endpoint = pending_api.pop(request_id, None)
                if not isinstance(request_id, str) or endpoint is None:
                    continue
                try:
                    body_value = await cdp.command(
                        "Network.getResponseBody",
                        {"requestId": request_id},
                    )
                except (RuntimeError, TimeoutError):
                    errors["room-api-body-unavailable"] += 1
                    continue
                raw_body = _decode_cdp_response_body(body_value)
                if raw_body is None:
                    errors["room-api-body-invalid-or-too-large"] += 1
                    continue
                try:
                    parsed = inspect_live_page(
                        raw_body,
                        room_url=room_url,
                        http_status=endpoint[3],
                        final_url=room_url,
                    )
                except ValueError:
                    errors["room-api-parse-failed"] += 1
                    continue
                api_state_counts[parsed.snapshot.live_state] += 1
                for candidate in parsed.candidates:
                    _merge_candidate(api_candidates, candidate.to_public_dict())
            await cdp.close()
            cdp = None
    except Exception as exc:
        errors[type(exc).__name__] += 1
    finally:
        if cdp is not None:
            await cdp.close()
        if process.returncode is None:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        user_data.cleanup()

    report = {
        "schema_version": 1,
        "room_id": room_id,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "status": "candidates-observed"
        if api_candidates or media_candidates
        else "no-candidates-observed",
        "page_loaded": page_loaded,
        "browser_started": True,
        "chrome_executable_name": Path(chrome_executable).name,
        "room_api_endpoint_counts": dict(sorted(api_endpoint_counts.items())),
        "room_api_status_counts": dict(sorted(api_status_counts.items())),
        "room_api_live_state_counts": dict(sorted(api_state_counts.items())),
        "room_api_candidate_count": len(api_candidates),
        "room_api_candidates": sorted(
            api_candidates.values(),
            key=lambda item: str(item.get("url_sha256", "")),
        ),
        "media_request_candidate_count": len(media_candidates),
        "media_request_candidates": sorted(
            media_candidates.values(),
            key=lambda item: str(item.get("url_sha256", "")),
        ),
        "error_counts": dict(sorted(errors.items())),
        "notes": [
            "Only two allowlisted Douyin room API paths may have response bodies inspected.",
            "The report never stores cookies, query values, response bodies, or full media URLs.",
            "Candidate metadata is limited to host, suffix, query keys, and path/url SHA-256.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Observe allowlisted Douyin room API and media requests through Chrome/CDP"
    )
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--chrome", default=os.getenv("CHROME_BIN", ""))
    parser.add_argument("--debug-port", type=int, default=9333)
    return parser


async def async_main() -> int:
    args = build_parser().parse_args()
    report = await run_network_probe(
        room_id=args.room_id.strip(),
        output=args.output,
        duration_seconds=args.duration,
        chrome=args.chrome,
        debug_port=args.debug_port,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())

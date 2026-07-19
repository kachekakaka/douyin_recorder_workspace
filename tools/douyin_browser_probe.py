from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

import websockets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.douyin import TARGET_METHOD  # noqa: E402
from app.douyin.envelope import EnvelopeDecodeError, inspect_frame  # noqa: E402
from app.douyin.recipient import (  # noqa: E402
    DecodedRecipientEvent,
    RecipientContract,
    RecipientDecodeError,
    decode_recipient_payload,
)

_CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)
_ALLOWED_WSS_SUFFIX = "douyin.com"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _chrome_executable(configured: str) -> str | None:
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        resolved = shutil.which(configured)
        if resolved:
            return resolved
    for name in _CHROME_CANDIDATES:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def _safe_ws_endpoint(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme != "wss" or not parsed.hostname:
        return None
    host = parsed.hostname.casefold().rstrip(".")
    if not (host == _ALLOWED_WSS_SUFFIX or host.endswith(f".{_ALLOWED_WSS_SUFFIX}")):
        return None
    return host, parsed.path or "/"


def _http_json(url: str) -> Any:
    with urlopen(url, timeout=2.0) as response:
        return json.loads(response.read())


async def _wait_for_page_target(port: int, timeout: float = 15.0) -> str:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            targets = await asyncio.to_thread(_http_json, f"http://127.0.0.1:{port}/json/list")
            if isinstance(targets, list):
                for target in targets:
                    if isinstance(target, dict) and target.get("type") == "page":
                        value = target.get("webSocketDebuggerUrl")
                        if isinstance(value, str) and value.startswith("ws://127.0.0.1:"):
                            return value
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(0.2)
    raise RuntimeError(f"Chrome DevTools endpoint 未就绪: {last_error}")


class CdpClient:
    def __init__(self, websocket: Any):
        self.websocket = websocket
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
        self._reader_task = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        try:
            async for raw in self.websocket:
                if not isinstance(raw, str):
                    continue
                message = json.loads(raw)
                identifier = message.get("id")
                if isinstance(identifier, int):
                    future = self._pending.pop(identifier, None)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                if self.events.full():
                    with suppress(asyncio.QueueEmpty):
                        self.events.get_nowait()
                await self.events.put(message)
        except Exception as exc:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(exc)
            self._pending.clear()

    async def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        identifier = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[identifier] = future
        await self.websocket.send(
            json.dumps({"id": identifier, "method": method, "params": params or {}})
        )
        response = await asyncio.wait_for(future, timeout=10.0)
        if "error" in response:
            raise RuntimeError(f"CDP {method} 失败: {response['error']}")
        return dict(response.get("result") or {})

    async def close(self) -> None:
        self._reader_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._reader_task


def _decode_binary_payload(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None


def _safe_event_summary(events: list[DecodedRecipientEvent]) -> dict[str, object]:
    recipient_hashes: set[str] = set()
    open_id_present = 0
    empty = 0
    reasons: Counter[str] = Counter()
    delays: list[int] = []
    unknown_fields: set[int] = set()
    for event in events:
        if event.recipient_key:
            recipient_hashes.add(hashlib.sha256(event.recipient_key.encode()).hexdigest()[:16])
        else:
            empty += 1
        if event.recipient_user_open_id:
            open_id_present += 1
        if event.change_reason_enum is not None:
            reasons[str(event.change_reason_enum)] += 1
        if event.delay_ms is not None:
            delays.append(event.delay_ms)
        for item in event.unknown_fields:
            field = item.get("field")
            if isinstance(field, int):
                unknown_fields.add(field)
    delays.sort()
    p95_index = min(len(delays) - 1, max(0, (95 * len(delays) + 99) // 100 - 1))
    p95 = delays[p95_index] if delays else None
    return {
        "decoded_event_count": len(events),
        "unique_recipient_hash_count": len(recipient_hashes),
        "empty_recipient_count": empty,
        "open_id_present_count": open_id_present,
        "change_reason_distribution": dict(sorted(reasons.items())),
        "unknown_target_field_numbers": sorted(unknown_fields),
        "server_delay_ms": {
            "count": len(delays),
            "min": delays[0] if delays else None,
            "p95": p95,
            "max": delays[-1] if delays else None,
        },
    }


async def run_browser_probe(
    *,
    room_id: str,
    output: Path,
    duration_seconds: float,
    contract_path: Path,
    chrome: str,
    debug_port: int,
) -> dict[str, object]:
    allowed_room_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    if not room_id or any(char not in allowed_room_chars for char in room_id):
        raise ValueError("room_id 格式无效")
    contract = RecipientContract.load(contract_path)
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
            "target_method": TARGET_METHOD,
            "contract_sha256": contract.sha256,
            "notes": ["未找到 Chrome/Chromium，未进行浏览器 WSS 现场探测。"],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report

    user_data = tempfile.TemporaryDirectory(prefix="douyin-browser-probe-")
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
        stderr=asyncio.subprocess.PIPE,
        start_new_session=start_new_session,
        creationflags=creationflags,
    )

    method_counts: Counter[str] = Counter()
    ws_requests: dict[str, tuple[str, str]] = {}
    websocket_endpoints: set[str] = set()
    errors: list[str] = []
    decoded_events: list[DecodedRecipientEvent] = []
    binary_frames = 0
    envelope_frames = 0
    parse_errors = 0
    target_messages = 0
    target_decode_failures = 0
    page_loaded = False
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
            deadline = time.monotonic() + max(5.0, duration_seconds)
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
                if method == "Network.webSocketCreated":
                    request_id = params.get("requestId")
                    url = params.get("url")
                    if isinstance(request_id, str) and isinstance(url, str):
                        safe = _safe_ws_endpoint(url)
                        if safe:
                            ws_requests[request_id] = safe
                            websocket_endpoints.add(f"wss://{safe[0]}{safe[1]}")
                    continue
                if method != "Network.webSocketFrameReceived":
                    continue
                request_id = params.get("requestId")
                if not isinstance(request_id, str) or request_id not in ws_requests:
                    continue
                response = params.get("response")
                if not isinstance(response, dict) or response.get("opcode") != 2:
                    continue
                payload_data = response.get("payloadData")
                if not isinstance(payload_data, str):
                    continue
                raw = _decode_binary_payload(payload_data)
                if raw is None:
                    errors.append("CDP binary frame 不是有效 base64")
                    continue
                binary_frames += 1
                try:
                    inspected = inspect_frame(raw)
                except EnvelopeDecodeError:
                    parse_errors += 1
                    continue
                envelope_frames += 1
                received_at_ms = int(time.time() * 1000)
                received_monotonic_ns = time.monotonic_ns()
                for message in inspected.response.messages:
                    method_counts[message.method] += 1
                    if message.method != TARGET_METHOD:
                        continue
                    target_messages += 1
                    try:
                        decoded = decode_recipient_payload(
                            message.payload,
                            contract=contract,
                            received_at_ms=received_at_ms,
                            received_monotonic_ns=received_monotonic_ns,
                            runtime_instance_id="github-actions-browser-probe",
                            envelope_msg_id=message.msg_id,
                        )
                    except RecipientDecodeError:
                        target_decode_failures += 1
                        continue
                    decoded_events.append(decoded)
            await cdp.close()
            cdp = None
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
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
        stderr = b""
        if process.stderr is not None:
            stderr = await process.stderr.read()
        if process.returncode not in (0, None) and stderr:
            first = stderr.decode("utf-8", errors="replace").splitlines()[:3]
            errors.extend(f"chrome: {line[:300]}" for line in first)
        user_data.cleanup()

    report = {
        "schema_version": 1,
        "room_id": room_id,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "status": "target-observed" if decoded_events else "no-target-observed",
        "page_loaded": page_loaded,
        "browser_started": True,
        "chrome_executable_name": Path(chrome_executable).name,
        "websocket_count": len(ws_requests),
        "sanitized_websocket_endpoints": sorted(websocket_endpoints),
        "binary_frame_count": binary_frames,
        "envelope_frame_count": envelope_frames,
        "envelope_parse_error_count": parse_errors,
        "message_count": sum(method_counts.values()),
        "method_counts": dict(sorted(method_counts.items())),
        "target_method": TARGET_METHOD,
        "target_message_count": target_messages,
        "target_decode_success_count": len(decoded_events),
        "target_decode_failure_count": target_decode_failures,
        "transport_live_verified": binary_frames > 0,
        "target_live_verified": len(decoded_events) > 0,
        "contract_live_verified_before_probe": contract.live_verified,
        "contract_sha256": contract.sha256,
        "field_report": _safe_event_summary(decoded_events),
        "errors": errors[:20],
        "notes": [
            "报告不保存 Cookie、完整签名 WSS URL、原始帧或 recipient 明文 ID。",
            "未观察到目标消息不能证明目标房间永远不下发；可能离线、受风控、窗口过短或尚未发生切换。",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通过无登录 Chrome/CDP 安全观察抖音直播 WSS method"
    )
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=45.0)
    parser.add_argument("--chrome", default=os.getenv("CHROME_BIN", ""))
    parser.add_argument("--debug-port", type=int, default=9222)
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "app" / "douyin" / "contracts" / "provisional_v1.json",
    )
    return parser


async def async_main() -> int:
    args = build_parser().parse_args()
    report = await run_browser_probe(
        room_id=args.room_id.strip(),
        output=args.output,
        duration_seconds=args.duration,
        contract_path=args.contract,
        chrome=args.chrome,
        debug_port=args.debug_port,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())

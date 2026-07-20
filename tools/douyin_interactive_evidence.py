from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

import websockets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.douyin.evidence import (  # noqa: E402
    EvidenceBundle,
    EvidenceError,
    EvidenceLimits,
)
from app.douyin.recipient import RecipientContract  # noqa: E402

_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
_MAX_CDP_MESSAGE_BYTES = 16 * 1024 * 1024


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def validate_devtools_url(value: str) -> tuple[str, int, str]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (ValueError, UnicodeError) as exc:
        raise EvidenceError("DevTools URL 无效") from exc
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "http" or host not in _LOOPBACK_HOSTS:
        raise EvidenceError("DevTools endpoint 只允许 http://127.0.0.1 或 http://[::1]")
    if parsed.username is not None or parsed.password is not None:
        raise EvidenceError("DevTools URL 不得包含凭据")
    if port is None or not 1 <= port <= 65535:
        raise EvidenceError("DevTools URL 必须包含有效端口")
    if parsed.query or parsed.fragment:
        raise EvidenceError("DevTools URL 不得包含 query 或 fragment")
    path = parsed.path.rstrip("/")
    if path:
        raise EvidenceError("DevTools URL 必须指向服务根路径")
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return host, port, f"http://{netloc}"


def validate_devtools_websocket(value: str, *, host: str, port: int) -> str:
    try:
        parsed = urlsplit(value)
        ws_port = parsed.port
    except (ValueError, UnicodeError) as exc:
        raise EvidenceError("DevTools WebSocket URL 无效") from exc
    ws_host = (parsed.hostname or "").casefold()
    if parsed.scheme != "ws" or ws_host != host or ws_port != port:
        raise EvidenceError("DevTools WebSocket 必须与回环 HTTP endpoint 同主机同端口")
    if parsed.username is not None or parsed.password is not None:
        raise EvidenceError("DevTools WebSocket 不得包含凭据")
    if not parsed.path.startswith("/devtools/page/"):
        raise EvidenceError("只允许附加到 page DevTools target")
    if parsed.query or parsed.fragment:
        raise EvidenceError("DevTools WebSocket 不得包含 query 或 fragment")
    return value


def canonical_room_url(room_id: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    if not room_id or any(char not in allowed for char in room_id):
        raise EvidenceError("room_id 格式无效")
    return f"https://live.douyin.com/{room_id}"


def validate_page_url(value: str, *, room_url: str) -> str:
    try:
        parsed = urlsplit(value)
    except (ValueError, UnicodeError) as exc:
        raise EvidenceError("页面 URL 无效") from exc
    expected = urlsplit(room_url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold().rstrip(".") != "live.douyin.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path.rstrip("/") != expected.path.rstrip("/")
        or parsed.query
        or parsed.fragment
    ):
        raise EvidenceError("DevTools page target 不匹配已授权直播间")
    return room_url


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _fetch_json(url: str, timeout: float = 3.0) -> Any:
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with opener.open(url, timeout=timeout) as response:
        if response.status != 200:
            raise EvidenceError(f"DevTools HTTP 返回 {response.status}")
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise EvidenceError("DevTools target 列表超过 2MiB")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceError("DevTools target 列表不是有效 JSON") from exc


def select_page_target(
    targets: object,
    *,
    room_url: str,
    devtools_host: str,
    devtools_port: int,
) -> str:
    if not isinstance(targets, list):
        raise EvidenceError("DevTools /json/list 必须返回数组")
    matches: list[str] = []
    for item in targets:
        if not isinstance(item, dict) or item.get("type") != "page":
            continue
        url = item.get("url")
        websocket_url = item.get("webSocketDebuggerUrl")
        if not isinstance(url, str) or not isinstance(websocket_url, str):
            continue
        try:
            validate_page_url(url, room_url=room_url)
            matches.append(
                validate_devtools_websocket(
                    websocket_url,
                    host=devtools_host,
                    port=devtools_port,
                )
            )
        except EvidenceError:
            continue
    if len(matches) != 1:
        raise EvidenceError("必须且只能找到一个精确匹配授权直播间的 page target")
    return matches[0]


class CdpClient:
    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10_000)
        self._reader_error: BaseException | None = None
        self._closing = False
        self._reader_task = asyncio.create_task(self._reader(), name="interactive-evidence-cdp")

    async def _reader(self) -> None:
        try:
            async for raw in self.websocket:
                if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_CDP_MESSAGE_BYTES:
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                identifier = message.get("id")
                if isinstance(identifier, int):
                    future = self._pending.pop(identifier, None)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                if self.events.full():
                    raise EvidenceError("CDP event queue 超过上限")
                await self.events.put(message)
        except Exception as exc:
            self._reader_error = exc
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(exc)
            self._pending.clear()

    def raise_if_failed(self) -> None:
        if self._reader_error is not None and not self._closing:
            raise EvidenceError("CDP reader 已失败") from self._reader_error

    async def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.raise_if_failed()
        identifier = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[identifier] = future
        await self.websocket.send(
            json.dumps({"id": identifier, "method": method, "params": params or {}})
        )
        try:
            response = await asyncio.wait_for(future, timeout=10.0)
        finally:
            self._pending.pop(identifier, None)
        error = response.get("error")
        if error:
            raise EvidenceError(f"CDP {method} 返回错误")
        result = response.get("result")
        return dict(result) if isinstance(result, dict) else {}

    async def close(self) -> None:
        self._closing = True
        self._reader_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._reader_task
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()


def _main_frame_url(params: dict[str, Any]) -> str | None:
    frame = params.get("frame")
    if not isinstance(frame, dict) or frame.get("parentId") is not None:
        return None
    url = frame.get("url")
    return url if isinstance(url, str) else None


def process_cdp_event(
    event: dict[str, Any],
    *,
    bundle: EvidenceBundle,
    room_url: str,
    accepted_requests: set[str],
    rejected_websocket_count: list[int],
) -> None:
    method = event.get("method")
    params = event.get("params")
    if not isinstance(params, dict):
        return
    if method == "Page.frameNavigated":
        url = _main_frame_url(params)
        if url is not None:
            validate_page_url(url, room_url=room_url)
        return
    if method == "Network.webSocketCreated":
        request_id = params.get("requestId")
        url = params.get("url")
        if not isinstance(request_id, str) or not isinstance(url, str):
            return
        try:
            bundle.register_websocket(request_id, url)
        except EvidenceError:
            rejected_websocket_count[0] += 1
        else:
            accepted_requests.add(request_id)
        return
    if method != "Network.webSocketFrameReceived":
        return
    request_id = params.get("requestId")
    response = params.get("response")
    if not isinstance(request_id, str) or request_id not in accepted_requests:
        return
    if not isinstance(response, dict) or response.get("opcode") != 2:
        return
    payload_data = response.get("payloadData")
    if not isinstance(payload_data, str):
        return
    try:
        raw = base64.b64decode(payload_data, validate=True)
    except (ValueError, TypeError) as exc:
        raise EvidenceError("CDP binary frame 不是有效 base64") from exc
    bundle.capture_binary_frame(
        raw,
        received_at_ms=int(time.time() * 1000),
        received_monotonic_ns=time.monotonic_ns(),
    )


async def run_interactive_probe(
    *,
    room_id: str,
    devtools_url: str,
    output_dir: Path,
    duration_seconds: float,
    contract_path: Path,
    repository_root: Path = ROOT,
    allow_private_output: bool = False,
    limits: EvidenceLimits | None = None,
    target_fetcher: Callable[[str], object] | None = None,
) -> dict[str, object]:
    room_url = canonical_room_url(room_id)
    devtools_host, devtools_port, base_url = validate_devtools_url(devtools_url)
    fetcher = target_fetcher or _fetch_json
    targets = await asyncio.to_thread(fetcher, f"{base_url}/json/list")
    debugger_url = select_page_target(
        targets,
        room_url=room_url,
        devtools_host=devtools_host,
        devtools_port=devtools_port,
    )
    contract = RecipientContract.load(contract_path)
    effective_limits = limits or EvidenceLimits(max_duration_seconds=duration_seconds)
    if not 1 <= duration_seconds <= effective_limits.max_duration_seconds:
        raise EvidenceError("duration 超过 evidence limits")
    bundle = EvidenceBundle(
        output_dir=output_dir,
        repository_root=repository_root,
        room_id=room_id,
        contract=contract,
        limits=effective_limits,
        allow_private_output=allow_private_output,
    )
    bundle.prepare()
    started_at = _utc_now()
    accepted_requests: set[str] = set()
    rejected = [0]
    errors: list[str] = []
    status = "completed-no-target"
    cdp: CdpClient | None = None
    try:
        async with websockets.connect(
            debugger_url,
            max_size=_MAX_CDP_MESSAGE_BYTES,
            open_timeout=10,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
            proxy=None,
        ) as websocket:
            cdp = CdpClient(websocket)
            await cdp.command(
                "Network.enable",
                {"maxTotalBufferSize": 0, "maxResourceBufferSize": 0},
            )
            await cdp.command("Page.enable")
            deadline = asyncio.get_running_loop().time() + duration_seconds
            while asyncio.get_running_loop().time() < deadline:
                timeout = min(1.0, max(0.05, deadline - asyncio.get_running_loop().time()))
                try:
                    event = await asyncio.wait_for(cdp.events.get(), timeout=timeout)
                except TimeoutError:
                    cdp.raise_if_failed()
                    continue
                cdp.raise_if_failed()
                process_cdp_event(
                    event,
                    bundle=bundle,
                    room_url=room_url,
                    accepted_requests=accepted_requests,
                    rejected_websocket_count=rejected,
                )
                if len(bundle.frame_records) >= bundle.limits.max_frames:
                    break
            if bundle.target_records:
                status = "target-observed"
            elif bundle.frame_records:
                status = "transport-observed"
    except EvidenceError as exc:
        status = "failed"
        errors.append(type(exc).__name__)
    except (OSError, TimeoutError, websockets.WebSocketException) as exc:
        status = "connection-failed"
        errors.append(type(exc).__name__)
    finally:
        if cdp is not None:
            await cdp.close()
    report = bundle.finalize(
        started_at=started_at,
        ended_at=_utc_now(),
        page_url_sha256=hashlib.sha256(room_url.encode("utf-8")).hexdigest(),
        status=status,
        errors=errors,
        public_extra={"rejected_non_allowlist_websocket_count": rejected[0]},
    )
    if status in {"failed", "connection-failed"}:
        raise EvidenceError(f"交互证据探测失败: {status}")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="附加到用户启动的回环 Chrome DevTools，被动保存私人 WSS 证据"
    )
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--devtools", default="http://127.0.0.1:9222")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--max-frames", type=int, default=500)
    parser.add_argument("--max-frame-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--max-total-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--allow-private-output", action="store_true")
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "app" / "douyin" / "contracts" / "provisional_v1.json",
    )
    return parser


async def async_main() -> int:
    args = build_parser().parse_args()
    limits = EvidenceLimits(
        max_frames=args.max_frames,
        max_frame_bytes=args.max_frame_bytes,
        max_total_bytes=args.max_total_bytes,
        max_duration_seconds=args.duration,
    )
    report = await run_interactive_probe(
        room_id=args.room_id.strip(),
        devtools_url=args.devtools,
        output_dir=args.output,
        duration_seconds=args.duration,
        contract_path=args.contract,
        allow_private_output=args.allow_private_output,
        limits=limits,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main())
    except EvidenceError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

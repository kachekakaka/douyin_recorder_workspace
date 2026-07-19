from __future__ import annotations

import asyncio
import base64
import http.client
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import websockets

from app.douyin.live_page import (
    MAX_BODY_BYTES,
    LivePageResult,
    StreamCandidate,
    inspect_live_page,
    normalize_room_reference,
    select_stream_candidate,
    stream_candidate_from_url,
)

_CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)
_ROOM_API_ENDPOINTS = {
    ("live.douyin.com", "/webcast/room/web/enter"),
    ("webcast.amemv.com", "/webcast/room/reflow/info"),
}
_MAX_BROWSER_CANDIDATES = 64


class LivePageChecker(Protocol):
    async def check(self, room_reference: str) -> LivePageResult: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class BrowserObservation:
    candidates: tuple[StreamCandidate, ...] = field(default=(), repr=False)
    page_loaded: bool = False
    page_http_status: int | None = None
    final_host: str = ""
    final_path: str = "/"
    room_api_response_count: int = 0
    error_code: str | None = None


BrowserObserver = Callable[[str, float], Awaitable[BrowserObservation]]


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    candidates: tuple[StreamCandidate, ...] = field(repr=False)
    expires_at: float


class _CdpClient:
    def __init__(self, websocket: Any) -> None:
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

    async def command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        identifier = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[identifier] = future
        await self.websocket.send(
            json.dumps({"id": identifier, "method": method, "params": params or {}})
        )
        response = await asyncio.wait_for(future, timeout=timeout)
        if "error" in response:
            raise RuntimeError(f"CDP command failed: {method}")
        return dict(response.get("result") or {})

    async def close(self) -> None:
        self._reader_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._reader_task


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


def _http_json(url: str) -> Any:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.path != "/json/list"
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise ValueError("DevTools discovery URL must stay on 127.0.0.1/json/list")
    connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=2.0)
    try:
        connection.request("GET", "/json/list", headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("DevTools discovery returned non-200")
        return json.loads(response.read())
    finally:
        connection.close()


async def _wait_for_page_target(profile_dir: Path, timeout: float = 15.0) -> str:
    deadline = time.monotonic() + timeout
    active_port = profile_dir / "DevToolsActivePort"
    while time.monotonic() < deadline:
        try:
            lines = active_port.read_text(encoding="utf-8").splitlines()
            port = int(lines[0])
            if not 1 <= port <= 65535:
                raise ValueError("invalid DevTools port")
            targets = await asyncio.to_thread(
                _http_json,
                f"http://127.0.0.1:{port}/json/list",
            )
            if isinstance(targets, list):
                prefix = f"ws://127.0.0.1:{port}/"
                for target in targets:
                    if not isinstance(target, dict) or target.get("type") != "page":
                        continue
                    value = target.get("webSocketDebuggerUrl")
                    if isinstance(value, str) and value.startswith(prefix):
                        return value
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        await asyncio.sleep(0.2)
    raise RuntimeError("Chrome DevTools endpoint did not become ready")


def _safe_room_api_endpoint(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (ValueError, UnicodeError):
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    path = (parsed.path or "/").rstrip("/") or "/"
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.fragment
        and (host, path) in _ROOM_API_ENDPOINTS
    )


def _decode_response_body(value: dict[str, Any]) -> bytes | None:
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
    return raw if len(raw) <= MAX_BODY_BYTES else None


async def _stop_browser(process: asyncio.subprocess.Process, cdp: _CdpClient | None) -> None:
    if cdp is not None:
        with suppress(Exception):
            await cdp.command("Browser.close", timeout=3.0)
    if process.returncode is not None:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=3.0)
        return
    except TimeoutError:
        pass

    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        with suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3.0)
        return
    except TimeoutError:
        pass

    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        with suppress(ProcessLookupError):
            process.kill()
    with suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=3.0)


async def observe_stream_candidates_with_chrome(
    room_url: str,
    duration_seconds: float,
    *,
    chrome: str = "",
) -> BrowserObservation:
    reference = normalize_room_reference(room_url)
    if not 3 <= duration_seconds <= 60:
        raise ValueError("browser observation duration must be between 3 and 60 seconds")
    executable = _chrome_executable(chrome or os.getenv("CHROME_BIN", ""))
    if executable is None:
        return BrowserObservation(error_code="chrome_not_found")

    user_data = tempfile.TemporaryDirectory(prefix="douyin-stream-resolver-")
    profile_dir = Path(user_data.name)
    command = [
        executable,
        "--headless=new",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-sync",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile_dir}",
        "about:blank",
    ]
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
        command.insert(1, "--no-sandbox")
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

    cdp: _CdpClient | None = None
    page_loaded = False
    page_http_status: int | None = None
    api_response_count = 0
    candidates: dict[str, StreamCandidate] = {}
    pending_api: dict[str, int] = {}
    final_host = ""
    final_path = "/"
    error_code: str | None = None

    try:
        debugger_url = await _wait_for_page_target(profile_dir)
        async with websockets.connect(
            debugger_url,
            max_size=8 * 1024 * 1024,
            open_timeout=10.0,
        ) as websocket:
            cdp = _CdpClient(websocket)
            await cdp.command(
                "Network.enable",
                {"maxTotalBufferSize": 0, "maxResourceBufferSize": 0},
            )
            await cdp.command("Page.enable")
            await cdp.command("Runtime.enable")
            await cdp.command("Page.navigate", {"url": reference.room_url})
            deadline = time.monotonic() + duration_seconds
            while time.monotonic() < deadline:
                timeout = min(0.75, max(0.05, deadline - time.monotonic()))
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
                    resource_type = params.get("type")
                    if not isinstance(request_id, str) or not isinstance(response, dict):
                        continue
                    url = response.get("url")
                    status = response.get("status")
                    if not isinstance(url, str) or not isinstance(status, (int, float)):
                        continue
                    status_code = int(status)
                    if resource_type == "Document":
                        with suppress(ValueError):
                            document_reference = normalize_room_reference(url)
                            if document_reference.room_url == reference.room_url:
                                page_http_status = status_code
                    if 200 <= status_code < 300:
                        candidate = stream_candidate_from_url(
                            url,
                            source_path="browser/network-response",
                        )
                        if candidate is not None and len(candidates) < _MAX_BROWSER_CANDIDATES:
                            candidates.setdefault(candidate.url, candidate)
                    if _safe_room_api_endpoint(url) and 200 <= status_code < 300:
                        pending_api[request_id] = status_code
                        api_response_count += 1
                    continue
                if method == "Network.loadingFailed":
                    request_id = params.get("requestId")
                    if isinstance(request_id, str):
                        pending_api.pop(request_id, None)
                    continue
                if method != "Network.loadingFinished":
                    continue
                request_id = params.get("requestId")
                if not isinstance(request_id, str):
                    continue
                status_code = pending_api.pop(request_id, None)
                if status_code is None:
                    continue
                try:
                    body_result = await cdp.command(
                        "Network.getResponseBody",
                        {"requestId": request_id},
                    )
                except (RuntimeError, TimeoutError):
                    continue
                raw_body = _decode_response_body(body_result)
                if raw_body is None:
                    continue
                try:
                    parsed = inspect_live_page(
                        raw_body,
                        room_url=reference.room_url,
                        http_status=status_code,
                        final_url=reference.room_url,
                    )
                except ValueError:
                    continue
                for candidate in parsed.candidates:
                    if len(candidates) >= _MAX_BROWSER_CANDIDATES:
                        break
                    candidates.setdefault(candidate.url, candidate)

            location_result = await cdp.command(
                "Runtime.evaluate",
                {"expression": "location.href", "returnByValue": True},
            )
            remote_value = location_result.get("result")
            location = remote_value.get("value") if isinstance(remote_value, dict) else None
            if not isinstance(location, str):
                raise RuntimeError("browser final URL unavailable")
            final_reference = normalize_room_reference(location)
            if final_reference.room_url != reference.room_url:
                raise RuntimeError("browser final room mismatch")
            final = urlsplit(final_reference.room_url)
            final_host = (final.hostname or "").casefold()
            final_path = final.path or "/"
            await cdp.close()
            cdp = None
    except Exception as exc:
        candidates.clear()
        error_code = f"browser_{type(exc).__name__.casefold()}"
    finally:
        await _stop_browser(process, cdp)
        user_data.cleanup()

    return BrowserObservation(
        candidates=tuple(
            sorted(
                candidates.values(),
                key=lambda item: (item.protocol, item.quality, item.host, item.url),
            )
        ),
        page_loaded=page_loaded,
        page_http_status=page_http_status,
        final_host=final_host,
        final_path=final_path,
        room_api_response_count=api_response_count,
        error_code=error_code,
    )


class DouyinStreamResolver:
    def __init__(
        self,
        live_page_client: LivePageChecker,
        *,
        browser_observer: BrowserObserver | None = None,
        browser_observation_seconds: float = 12.0,
        browser_timeout_seconds: float = 30.0,
        cache_ttl_seconds: float = 120.0,
        max_cached_rooms: int = 32,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 3 <= browser_observation_seconds <= 60:
            raise ValueError("browser_observation_seconds must be between 3 and 60")
        if browser_timeout_seconds < browser_observation_seconds or browser_timeout_seconds > 120:
            raise ValueError("browser_timeout_seconds must cover observation and stay under 120")
        if not 5 <= cache_ttl_seconds <= 900:
            raise ValueError("cache_ttl_seconds must be between 5 and 900")
        if not 1 <= max_cached_rooms <= 256:
            raise ValueError("max_cached_rooms must be between 1 and 256")
        self.live_page_client = live_page_client
        self.browser_observer = browser_observer or observe_stream_candidates_with_chrome
        self.browser_observation_seconds = browser_observation_seconds
        self.browser_timeout_seconds = browser_timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_cached_rooms = max_cached_rooms
        self._monotonic = monotonic
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._browser_lock = asyncio.Lock()
        self._closed = False

    def _purge_expired(self) -> None:
        now = self._monotonic()
        expired = [room_url for room_url, entry in self._cache.items() if entry.expires_at <= now]
        for room_url in expired:
            self._cache.pop(room_url, None)

    @staticmethod
    def _validated_candidates(
        candidates: tuple[StreamCandidate, ...],
    ) -> tuple[StreamCandidate, ...]:
        output: dict[str, StreamCandidate] = {}
        for candidate in candidates[:_MAX_BROWSER_CANDIDATES]:
            validated = stream_candidate_from_url(
                candidate.url,
                source_path=candidate.source_path,
                quality_hint=candidate.quality,
            )
            if validated is not None:
                output.setdefault(validated.url, validated)
        return tuple(
            sorted(
                output.values(),
                key=lambda item: (item.protocol, item.quality, item.host, item.url),
            )
        )

    def _store(self, room_url: str, candidates: tuple[StreamCandidate, ...]) -> None:
        self._purge_expired()
        self._cache.pop(room_url, None)
        self._cache[room_url] = _CacheEntry(
            candidates=candidates,
            expires_at=self._monotonic() + self.cache_ttl_seconds,
        )
        while len(self._cache) > self.max_cached_rooms:
            self._cache.popitem(last=False)

    def cached_candidates(self, room_reference: str) -> tuple[StreamCandidate, ...]:
        reference = normalize_room_reference(room_reference)
        self._purge_expired()
        entry = self._cache.get(reference.room_url)
        if entry is None:
            return ()
        self._cache.move_to_end(reference.room_url)
        return entry.candidates

    def discard(self, room_reference: str) -> None:
        reference = normalize_room_reference(room_reference)
        self._cache.pop(reference.room_url, None)

    def select_cached_candidate(
        self,
        room_reference: str,
        *,
        protocol: str,
        quality: str,
    ) -> StreamCandidate | None:
        return select_stream_candidate(
            self.cached_candidates(room_reference),
            protocol=protocol,
            quality=quality,
        )

    def clear(self) -> None:
        self._cache.clear()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.clear()
        await self.live_page_client.close()

    async def resolve(self, room_reference: str) -> LivePageResult:
        if self._closed:
            raise RuntimeError("stream resolver is closed")
        reference = normalize_room_reference(room_reference)
        static_result = await self.live_page_client.check(reference.room_url)
        static_candidates = self._validated_candidates(static_result.candidates)
        if static_candidates:
            self._store(reference.room_url, static_candidates)
            snapshot = replace(
                static_result.snapshot,
                live_state="live",
                stream_candidates=static_candidates,
            )
            return LivePageResult(snapshot=snapshot, candidates=static_candidates)
        if static_result.candidates:
            static_result = LivePageResult(
                snapshot=replace(
                    static_result.snapshot,
                    live_state="unknown",
                    stream_candidates=(),
                    notes=(
                        *static_result.snapshot.notes,
                        "静态解析候选未通过 resolver 二次 CDN 安全校验。",
                    ),
                ),
                candidates=(),
            )

        try:
            async with self._browser_lock:
                observation = await asyncio.wait_for(
                    self.browser_observer(
                        reference.room_url,
                        self.browser_observation_seconds,
                    ),
                    timeout=self.browser_timeout_seconds,
                )
        except TimeoutError:
            observation = BrowserObservation(error_code="browser_timeout")
        except Exception as exc:
            observation = BrowserObservation(
                error_code=f"browser_{type(exc).__name__.casefold()}"
            )

        browser_candidates = self._validated_candidates(observation.candidates)
        if not browser_candidates:
            self.discard(reference.room_url)
            note = (
                "一次性浏览器回退未观察到可用候选；未保存页面正文、Cookie、query value 或媒体 URL。"
            )
            snapshot = replace(
                static_result.snapshot,
                notes=(*static_result.snapshot.notes, note),
            )
            return LivePageResult(snapshot=snapshot, candidates=())

        candidates = browser_candidates
        self._store(reference.room_url, candidates)
        final_host = (observation.final_host or "").casefold().rstrip(".")
        safe_final_host = final_host if final_host == "live.douyin.com" else "live.douyin.com"
        expected_path = f"/{reference.room_id_hint}"
        safe_final_path = (
            observation.final_path
            if observation.final_path.rstrip("/") == expected_path
            else "/<redacted-path>"
        )
        snapshot = replace(
            static_result.snapshot,
            checked_at_ms=int(time.time() * 1000),
            live_state="live",
            http_status=observation.page_http_status or static_result.snapshot.http_status,
            final_host=safe_final_host,
            final_path=safe_final_path,
            stream_candidates=candidates,
            notes=(
                *static_result.snapshot.notes,
                "静态页面没有候选；一次性 Chrome/CDP 网络观察获得了受信任 CDN 媒体响应。",
                "完整媒体 URL 仅保留在有界、带 TTL 的当前进程内存缓存。",
            ),
            error_code=None,
        )
        return LivePageResult(snapshot=snapshot, candidates=candidates)

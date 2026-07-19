from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

import httpx

MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_JSON_STRING_BYTES = 2 * 1024 * 1024
MAX_JSON_NODES = 100_000
MAX_REDIRECTS = 5
MAX_STREAM_CANDIDATES = 64
MAX_TEXT_URL_MATCHES = 256
MAX_WSS_MATCHES = 256
MAX_STREAM_URL_CHARS = 16_384

_ROOM_REFERENCE_RE = re.compile(r"^[A-Za-z0-9_.-]{3,80}$")
_RENDER_DATA_RE = re.compile(
    r'<script[^>]+id=["\']RENDER_DATA["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_JSON_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_WSS_RE = re.compile(r"wss://[^\s\"'<>]+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE)

_ROOM_ID_KEYS = {
    "roomid",
    "room_id",
    "room_id_str",
}
_WEB_RID_KEYS = {"web_rid", "webrid"}
_TITLE_KEYS = {"title", "room_title"}
_STATUS_KEYS = {"status", "live_status", "room_status"}
_STREAM_KEY_HINTS = {
    "flv",
    "hls",
    "flv_pull_url",
    "hls_pull_url",
    "stream_data",
    "stream_url",
    "live_core_sdk_data",
    "pull_data",
    "main",
}
_QUALITY_ORDER = ("origin", "uhd", "hd", "sd", "ld", "md", "unknown")
_QUALITY_ALIASES = {
    "origin": "origin",
    "origin_1": "origin",
    "original": "origin",
    "uhd": "uhd",
    "ultra": "uhd",
    "hd": "hd",
    "high": "hd",
    "sd": "sd",
    "standard": "sd",
    "ld": "ld",
    "low": "ld",
    "md": "md",
    "medium": "md",
}
_SAFE_SOURCE_KEYS = {
    "app",
    "room",
    "stream_url",
    "live_core_sdk_data",
    "pull_data",
    "stream_data",
    "data",
    "main",
    "flv",
    "hls",
    "flv_pull_url",
    "hls_pull_url",
    *_QUALITY_ALIASES,
}
_ALLOWED_PAGE_HOSTS = {"live.douyin.com", "www.douyin.com", "douyin.com"}
_ALLOWED_STREAM_SUFFIXES = (
    "douyincdn.com",
    "douyin.com",
    "bytecdn.cn",
    "byteimg.com",
    "amemv.com",
    "snssdk.com",
    "zijieapi.com",
    "bytedance.com",
)
_BLOCK_MARKERS = (
    "verifycenter",
    "security verification",
    "访问频繁",
    "验证后继续",
    "请完成下列验证",
)
_SAFE_MARKERS = (
    "live_core_sdk_data",
    "stream_data",
    "flv_pull_url",
    "hls_pull_url",
    "web_rid",
    "roomid",
    "room_id",
    "webcast",
)


class LivePageError(ValueError):
    """Raised when a room reference or live-page response violates a safety boundary."""


@dataclass(frozen=True, slots=True)
class NormalizedRoomReference:
    room_url: str
    room_id_hint: str


@dataclass(frozen=True, slots=True)
class StreamCandidate:
    protocol: str
    quality: str
    url: str
    source_path: str

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").casefold()

    def to_public_dict(self) -> dict[str, object]:
        parsed = urlsplit(self.url)
        raw_path = parsed.path or "/"
        suffix = Path(raw_path).suffix.casefold()
        if suffix not in {".flv", ".m3u8"}:
            suffix = ""
        query_keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
        return {
            "protocol": self.protocol,
            "quality": self.quality,
            "host": self.host,
            "path_suffix": suffix,
            "path_sha256": hashlib.sha256(raw_path.encode()).hexdigest(),
            "query_keys": query_keys,
            "url_sha256": hashlib.sha256(self.url.encode()).hexdigest(),
            "source_path": self.source_path,
        }


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    room_url: str
    checked_at_ms: int
    live_state: str
    http_status: int | None
    final_host: str
    final_path: str
    external_room_id: str | None
    web_rid: str | None
    title: str
    body_sha256: str
    body_bytes_read: int
    body_truncated: bool
    blocked_markers: tuple[str, ...]
    marker_counts: dict[str, int]
    sanitized_websocket_endpoints: tuple[str, ...]
    stream_candidates: tuple[StreamCandidate, ...]
    notes: tuple[str, ...]
    error_code: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["stream_candidates"] = [item.to_public_dict() for item in self.stream_candidates]
        result["stream_candidate_count"] = len(self.stream_candidates)
        return result


@dataclass(frozen=True, slots=True)
class LivePageResult:
    snapshot: LiveSnapshot
    candidates: tuple[StreamCandidate, ...]


def normalize_room_reference(value: str) -> NormalizedRoomReference:
    raw = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise LivePageError("直播间地址包含控制字符")
    if _ROOM_REFERENCE_RE.fullmatch(raw):
        return NormalizedRoomReference(
            room_url=f"https://live.douyin.com/{raw}",
            room_id_hint=raw,
        )

    try:
        parsed = urlsplit(raw)
        port = parsed.port
        host = (parsed.hostname or "").casefold().rstrip(".")
    except ValueError as exc:
        raise LivePageError("直播间地址格式无效") from exc
    if parsed.scheme != "https" or not host:
        raise LivePageError("直播间地址必须是 https://live.douyin.com/<抖音号或房间标识>")
    if parsed.username is not None or parsed.password is not None or port not in (None, 443):
        raise LivePageError("直播间地址不得包含凭据或自定义端口")
    if host != "live.douyin.com":
        raise LivePageError("P1A 只允许 live.douyin.com 直播间地址")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1 or not _ROOM_REFERENCE_RE.fullmatch(parts[0]):
        raise LivePageError("直播间路径必须只包含一个抖音号或房间标识")
    return NormalizedRoomReference(
        room_url=urlunsplit(("https", host, f"/{parts[0]}", "", "")),
        room_id_hint=parts[0],
    )


def _normalize_key(value: object) -> str:
    return str(value).strip().casefold().replace("-", "_")


def _is_public_host(host: str) -> bool:
    normalized = host.casefold().rstrip(".")
    if not normalized or normalized == "localhost" or normalized.endswith(".local"):
        return False
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        return any(
            normalized == suffix or normalized.endswith(f".{suffix}")
            for suffix in _ALLOWED_STREAM_SUFFIXES
        )
    # FFmpeg follows the URL independently, so IP literals are rejected even when public.
    return False


def _normalize_stream_url(value: str) -> str | None:
    candidate = html.unescape(value).replace("\\u0026", "&").replace("\\/", "/")
    if len(candidate) > MAX_STREAM_URL_CHARS or any(
        ord(char) < 32 or ord(char) == 127 for char in candidate
    ):
        return None
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
        host = (parsed.hostname or "").casefold().rstrip(".")
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not host:
        return None
    expected_port = 80 if parsed.scheme == "http" else 443
    if (
        parsed.username is not None
        or parsed.password is not None
        or port not in (None, expected_port)
    ):
        return None
    if not _is_public_host(host):
        return None
    return urlunsplit((parsed.scheme, host, parsed.path or "/", parsed.query, ""))


def _sanitize_wss_url(value: str) -> str | None:
    candidate = html.unescape(value).replace("\\u0026", "&").replace("\\/", "/")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
        host = (parsed.hostname or "").casefold().rstrip(".")
    except ValueError:
        return None
    if (
        parsed.scheme != "wss"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return None
    if not (host == "douyin.com" or host.endswith(".douyin.com")):
        return None
    return f"wss://{host}{parsed.path or '/'}"


def _quality_from_path(path: tuple[str, ...]) -> str:
    for part in reversed(path):
        normalized = _normalize_key(part)
        if normalized in _QUALITY_ALIASES:
            return _QUALITY_ALIASES[normalized]
    return "unknown"


def _protocol_from_path_or_url(path: tuple[str, ...], url: str) -> str | None:
    joined = "/".join(_normalize_key(item) for item in path)
    parsed_path = urlsplit(url).path.casefold()
    if "flv" in joined or parsed_path.endswith(".flv"):
        return "flv"
    if "hls" in joined or parsed_path.endswith(".m3u8"):
        return "hls"
    return None


def _looks_like_json(value: str) -> bool:
    stripped = value.lstrip()
    within_limit = len(value.encode("utf-8", errors="ignore")) <= MAX_JSON_STRING_BYTES
    return within_limit and stripped[:1] in {"{", "["}


def _decoded_text_views(text: str) -> list[str]:
    views = [text]
    for match in _RENDER_DATA_RE.findall(text):
        candidate = html.unescape(match.strip())
        views.extend((candidate, unquote(candidate)))
    for match in _JSON_SCRIPT_RE.findall(text):
        views.append(html.unescape(match.strip()))
    return views


def _json_documents(text: str) -> list[Any]:
    documents: list[Any] = []
    for view in _decoded_text_views(text):
        if not _looks_like_json(view):
            continue
        try:
            value = json.loads(view)
        except (json.JSONDecodeError, TypeError):
            continue
        documents.append(value)
    return documents


def _walk_json(documents: list[Any]) -> tuple[list[tuple[tuple[str, ...], Any]], int]:
    rows: list[tuple[tuple[str, ...], Any]] = []
    stack: list[tuple[tuple[str, ...], Any, int]] = [((), document, 0) for document in documents]
    seen_strings: set[str] = set()
    visited = 0
    while stack:
        path, value, depth = stack.pop()
        visited += 1
        if visited > MAX_JSON_NODES:
            break
        rows.append((path, value))
        if depth >= 16:
            continue
        if isinstance(value, dict):
            for key, item in value.items():
                stack.append(((*path, str(key)), item, depth + 1))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                stack.append(((*path, str(index)), item, depth + 1))
        elif isinstance(value, str) and _looks_like_json(value) and value not in seen_strings:
            seen_strings.add(value)
            try:
                nested = json.loads(value)
            except json.JSONDecodeError:
                continue
            stack.append(((*path, "<json-string>"), nested, depth + 1))
    return rows, visited


def _first_scalar(
    rows: list[tuple[tuple[str, ...], Any]],
    keys: set[str],
    *,
    allow_zero: bool = False,
) -> str | None:
    for path, value in rows:
        if not path or _normalize_key(path[-1]) not in keys:
            continue
        if isinstance(value, (bool, dict, list)):
            continue
        text = str(value).strip()
        if text and (allow_zero or text != "0"):
            return text
    return None


def _safe_source_path(path: tuple[str, ...]) -> str:
    output: list[str] = []
    for part in path[-10:]:
        value = str(part)
        normalized = _normalize_key(value)
        if value in {"<text>", "<json-string>"} or (value.isdigit() and len(value) <= 4):
            output.append(value)
        elif normalized in _SAFE_SOURCE_KEYS:
            output.append(normalized)
        else:
            digest = hashlib.sha256(value.encode()).hexdigest()[:12]
            output.append(f"<key-{digest}>")
    return "/".join(output) or "<text>"


def _extract_candidates(
    rows: list[tuple[tuple[str, ...], Any]], text: str
) -> tuple[StreamCandidate, ...]:
    candidates: dict[str, StreamCandidate] = {}

    def add(path: tuple[str, ...], raw_url: str) -> None:
        normalized = _normalize_stream_url(raw_url)
        if normalized is None:
            return
        protocol = _protocol_from_path_or_url(path, normalized)
        if protocol is None:
            return
        path_hints = {_normalize_key(item) for item in path}
        has_stream_hint = any(hint in path_hints for hint in _STREAM_KEY_HINTS)
        has_media_suffix = urlsplit(normalized).path.casefold().endswith((".flv", ".m3u8"))
        if not has_stream_hint and not has_media_suffix:
            return
        quality = _quality_from_path(path)
        previous = candidates.get(normalized)
        if previous is None and len(candidates) >= MAX_STREAM_CANDIDATES:
            return
        current = StreamCandidate(
            protocol=protocol,
            quality=quality,
            url=normalized,
            source_path=_safe_source_path(path),
        )
        if previous is None or (
            _QUALITY_ORDER.index(current.quality) < _QUALITY_ORDER.index(previous.quality)
        ):
            candidates[normalized] = current

    for path, value in rows:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            add(path, value)
        elif isinstance(value, dict) and path:
            final_key = _normalize_key(path[-1])
            if final_key not in {"flv_pull_url", "hls_pull_url"}:
                continue
            for quality, raw_url in value.items():
                if isinstance(raw_url, str):
                    add((*path, str(quality)), raw_url)

    for index, match in enumerate(_URL_RE.finditer(text)):
        if index >= MAX_TEXT_URL_MATCHES:
            break
        add(("<text>",), match.group(0))

    return tuple(
        sorted(
            candidates.values(),
            key=lambda item: (
                0 if item.protocol == "flv" else 1,
                _QUALITY_ORDER.index(item.quality),
                item.host,
                item.url,
            ),
        )
    )


def _extract_status(rows: list[tuple[tuple[str, ...], Any]]) -> tuple[str | None, tuple[str, ...]]:
    values: list[str] = []
    for path, value in rows:
        if not path or _normalize_key(path[-1]) not in _STATUS_KEYS:
            continue
        if isinstance(value, (dict, list)):
            continue
        text = str(value).strip().casefold()
        if text and text not in values:
            values.append(text)
    return (values[0] if values else None), tuple(values[:20])


def inspect_live_page(
    body: bytes,
    *,
    room_url: str,
    http_status: int | None,
    final_url: str,
    body_truncated: bool = False,
    error_code: str | None = None,
) -> LivePageResult:
    reference = normalize_room_reference(room_url)
    try:
        final = urlsplit(final_url or reference.room_url)
        final_port = final.port
        final_host = (final.hostname or "").casefold().rstrip(".")
    except ValueError as exc:
        raise LivePageError("直播页最终地址格式无效") from exc
    if (
        final.scheme != "https"
        or final_host not in _ALLOWED_PAGE_HOSTS
        or final.username
        or final.password
        or final_port not in (None, 443)
    ):
        raise LivePageError(f"直播页最终地址不在允许范围: {final_host or '<empty>'}")

    text = body.decode("utf-8", errors="replace")
    decoded_text = "\n".join(_decoded_text_views(text))
    lower = decoded_text.casefold()
    documents = _json_documents(text)
    rows, visited_nodes = _walk_json(documents)
    candidates = _extract_candidates(rows, decoded_text)
    external_room_id = _first_scalar(rows, _ROOM_ID_KEYS)
    web_rid = _first_scalar(rows, _WEB_RID_KEYS)
    title = _first_scalar(rows, _TITLE_KEYS, allow_zero=True) or ""
    _status, status_values = _extract_status(rows)
    blocked_markers = tuple(marker for marker in _BLOCK_MARKERS if marker.casefold() in lower)
    marker_counts = {marker: lower.count(marker.casefold()) for marker in _SAFE_MARKERS}

    websocket_endpoints: list[str] = []
    for index, match in enumerate(_WSS_RE.finditer(decoded_text)):
        if index >= MAX_WSS_MATCHES:
            break
        safe = _sanitize_wss_url(match.group(0))
        if safe and safe not in websocket_endpoints:
            websocket_endpoints.append(safe)
        if len(websocket_endpoints) >= 20:
            break

    if error_code:
        live_state = "error"
    elif http_status is not None and not 200 <= http_status < 300:
        live_state = "error"
        error_code = f"http_{http_status}"
    elif blocked_markers:
        live_state = "blocked"
    elif candidates:
        live_state = "live"
    else:
        live_state = "unknown"
    usable_candidates = candidates if live_state == "live" else ()

    notes = (
        "完整签名流 URL 只保留在当前进程内存；公开结果仅返回 "
        "host、媒体后缀、path/url hash 和 query key。",
        "数字 status 尚未形成现场稳定映射；没有流候选时保持 unknown，不猜测 offline。",
        f"解析 JSON 节点数：{visited_nodes}；发现 status 值数量：{len(status_values)}。",
        "live_state=live 仅表示页面中解析出受限域名的 FLV/HLS 候选，不等于推荐收礼人协议已验证。",
    )
    snapshot = LiveSnapshot(
        room_url=reference.room_url,
        checked_at_ms=int(time.time() * 1000),
        live_state=live_state,
        http_status=http_status,
        final_host=final_host,
        final_path=final.path or "/",
        external_room_id=external_room_id,
        web_rid=web_rid,
        title=title[:500],
        body_sha256=hashlib.sha256(body).hexdigest(),
        body_bytes_read=len(body),
        body_truncated=body_truncated,
        blocked_markers=blocked_markers,
        marker_counts=marker_counts,
        sanitized_websocket_endpoints=tuple(websocket_endpoints),
        stream_candidates=usable_candidates,
        notes=notes,
        error_code=error_code,
    )
    return LivePageResult(snapshot=snapshot, candidates=usable_candidates)


class DouyinLivePageClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 25.0,
        max_body_bytes: int = MAX_BODY_BYTES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds 必须在 1–120 之间")
        if not 1024 <= max_body_bytes <= MAX_BODY_BYTES:
            raise ValueError(f"max_body_bytes 必须在 1024–{MAX_BODY_BYTES} 之间")
        self.timeout_seconds = timeout_seconds
        self.max_body_bytes = max_body_bytes
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
                "Cache-Control": "no-cache",
            },
            follow_redirects=False,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def check(self, room_reference: str) -> LivePageResult:
        reference = normalize_room_reference(room_reference)
        url = reference.room_url
        last_status: int | None = None
        try:
            for _ in range(MAX_REDIRECTS + 1):
                async with self._client.stream("GET", url, follow_redirects=False) as response:
                    last_status = response.status_code
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "").strip()
                        if not location:
                            raise LivePageError("直播页重定向缺少 Location")
                        try:
                            next_url = urljoin(url, location)
                            parsed = urlsplit(next_url)
                            port = parsed.port
                            host = (parsed.hostname or "").casefold().rstrip(".")
                        except ValueError as exc:
                            raise LivePageError("直播页重定向地址格式无效") from exc
                        if (
                            parsed.scheme != "https"
                            or host not in _ALLOWED_PAGE_HOSTS
                            or parsed.username
                            or parsed.password
                            or port not in (None, 443)
                        ):
                            raise LivePageError("直播页重定向离开允许的抖音主机或包含危险凭据")
                        url = urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
                        continue

                    body = bytearray()
                    truncated = False
                    async for chunk in response.aiter_bytes():
                        remaining = self.max_body_bytes + 1 - len(body)
                        if remaining <= 0:
                            truncated = True
                            break
                        body.extend(chunk[:remaining])
                        if len(body) > self.max_body_bytes:
                            truncated = True
                            del body[self.max_body_bytes :]
                            break
                    return inspect_live_page(
                        bytes(body),
                        room_url=reference.room_url,
                        http_status=response.status_code,
                        final_url=str(response.url),
                        body_truncated=truncated,
                    )
            raise LivePageError("直播页重定向次数超过上限")

        except (httpx.HTTPError, LivePageError) as exc:
            code = type(exc).__name__
            snapshot = LiveSnapshot(
                room_url=reference.room_url,
                checked_at_ms=int(time.time() * 1000),
                live_state="error",
                http_status=last_status,
                final_host=(urlsplit(url).hostname or "").casefold(),
                final_path=urlsplit(url).path or "/",
                external_room_id=None,
                web_rid=None,
                title="",
                body_sha256="",
                body_bytes_read=0,
                body_truncated=False,
                blocked_markers=(),
                marker_counts={},
                sanitized_websocket_endpoints=(),
                stream_candidates=(),
                notes=(
                    "网络或安全边界失败不等于房间离线；应在可访问抖音的环境重试。",
                    "错误详情不包含完整签名 URL、Cookie 或响应正文。",
                ),
                error_code=code,
            )
            return LivePageResult(snapshot=snapshot, candidates=())

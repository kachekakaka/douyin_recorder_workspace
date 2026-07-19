from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from collections import Counter
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import websockets
from websockets.exceptions import ConnectionClosed

from app.douyin.envelope import EnvelopeDecodeError, build_ack, build_heartbeat, inspect_frame
from app.douyin.protobuf_wire import MAX_MESSAGE_BYTES
from app.douyin.recipient import (
    TARGET_METHOD,
    DecodedRecipientEvent,
    RecipientContract,
    RecipientDecodeError,
    decode_recipient_payload,
)
from app.douyin.replay import summarize_recipient_events

DEFAULT_ALLOWED_HOST_SUFFIXES = ("douyin.com",)
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,80}$")
_FILE_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_PRIVATE_INPUT_BYTES = 64 * 1024


class ProbeConfigurationError(ValueError):
    """Raised when probe inputs violate privacy or target boundaries."""


@dataclass(frozen=True, slots=True)
class ProbeOptions:
    websocket_url: str
    output_dir: Path
    contract_path: Path
    room_url: str = ""
    origin: str = "https://live.douyin.com"
    cookie_file: Path | None = None
    header_file: Path | None = None
    duration_seconds: float = 60.0
    max_frames: int = 500
    send_ack: bool = False
    send_application_heartbeat: bool = False
    heartbeat_seconds: float = 10.0
    allowed_host_suffixes: tuple[str, ...] = DEFAULT_ALLOWED_HOST_SUFFIXES
    allow_insecure_local: bool = False


@dataclass(frozen=True, slots=True)
class ProbeReport:
    probe_id: str
    status: str
    transport_connected: bool
    transport_frames_received: bool
    transport_live_verified: bool
    synthetic_transport: bool
    contract_live_verified: bool
    transport_error: str
    websocket_host: str
    room_url: str
    started_at: str
    ended_at: str
    frame_count: int
    binary_frame_count: int
    text_frame_count: int
    parse_error_count: int
    message_count: int
    method_counts: dict[str, int]
    target_message_count: int
    target_decode_success_count: int
    target_decode_failure_count: int
    ack_sent_count: int
    heartbeat_sent_count: int
    contract_sha256: str
    field_report: dict[str, object]
    target_payload_files: tuple[str, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_websocket_url(
    url: str,
    allowed_suffixes: tuple[str, ...],
    *,
    allow_insecure_local: bool = False,
) -> str:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ProbeConfigurationError("WebSocket URL 必须包含主机名")
    if parsed.username or parsed.password:
        raise ProbeConfigurationError("WebSocket URL 不得包含用户名或密码")
    host = parsed.hostname.casefold().rstrip(".")
    if (
        allow_insecure_local
        and parsed.scheme == "ws"
        and host
        in {
            "127.0.0.1",
            "::1",
            "localhost",
        }
    ):
        return host
    if parsed.scheme != "wss":
        raise ProbeConfigurationError("现场探测只允许 wss:// URL")
    normalized = tuple(item.casefold().lstrip(".").rstrip(".") for item in allowed_suffixes)
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in normalized):
        raise ProbeConfigurationError(f"WebSocket 主机不在允许的抖音域名范围: {host}")
    return host


def sanitize_room_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProbeConfigurationError("room_url 必须是 http(s) URL")
    host = parsed.hostname.casefold().rstrip(".")
    if not (host == "douyin.com" or host.endswith(".douyin.com")):
        raise ProbeConfigurationError("room_url 必须位于 douyin.com")
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))


def _read_private_text(path: Path | None) -> str:
    if path is None:
        return ""
    if path.is_symlink() or not path.is_file():
        raise ProbeConfigurationError(f"私有输入文件不存在或类型不安全: {path}")
    if path.stat().st_size > _MAX_PRIVATE_INPUT_BYTES:
        raise ProbeConfigurationError("私有输入文件过大")
    return path.read_text(encoding="utf-8").strip()


def _load_headers(options: ProbeOptions) -> dict[str, str]:
    headers = {"User-Agent": "Mozilla/5.0 (douyin-recorder-p0-probe)"}
    if options.header_file is not None:
        try:
            values = json.loads(_read_private_text(options.header_file))
        except json.JSONDecodeError as exc:
            raise ProbeConfigurationError(f"header file JSON 无效: {exc}") from exc
        if not isinstance(values, dict) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in values.items()
        ):
            raise ProbeConfigurationError("header file 必须是字符串到字符串的 JSON 对象")
        for name, value in values.items():
            if not _HEADER_NAME_RE.fullmatch(name) or "\r" in value or "\n" in value:
                raise ProbeConfigurationError(f"请求头无效: {name!r}")
            if name.casefold() in {
                "cookie",
                "host",
                "origin",
                "connection",
                "content-length",
                "sec-websocket-key",
            }:
                raise ProbeConfigurationError(f"请求头 {name} 不能由 header file 覆盖")
            headers[name] = value
    cookie = _read_private_text(options.cookie_file) or os.getenv("DOUYIN_COOKIE", "").strip()
    if cookie:
        if "\r" in cookie or "\n" in cookie:
            raise ProbeConfigurationError("Cookie 不得包含换行")
        headers["Cookie"] = cookie
    return headers


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_token(value: str | None) -> str:
    token = _FILE_TOKEN_RE.sub("-", value or "no-id").strip("-._")
    return (token or "no-id")[:80]


def _prepare_output(path: Path) -> Path:
    if path.exists():
        raise ProbeConfigurationError(f"输出目录已存在，拒绝覆盖: {path}")
    output = path.resolve()
    output.mkdir(parents=True, exist_ok=False)
    return output


async def _heartbeat_loop(websocket: object, seconds: float, counter: list[int]) -> None:
    while True:
        await asyncio.sleep(seconds)
        # The application heartbeat is carried as WebSocket ping data, not as an
        # ordinary binary application message. It remains opt-in until a live room
        # capture confirms the current behavior.
        await websocket.ping(build_heartbeat())  # type: ignore[attr-defined]
        counter[0] += 1


async def run_probe(options: ProbeOptions) -> ProbeReport:
    host = validate_websocket_url(
        options.websocket_url,
        options.allowed_host_suffixes,
        allow_insecure_local=options.allow_insecure_local,
    )
    room_url = sanitize_room_url(options.room_url)
    if not 0 < options.duration_seconds <= 3600:
        raise ProbeConfigurationError("duration_seconds 必须在 0–3600 之间")
    if not 0 < options.max_frames <= 100_000:
        raise ProbeConfigurationError("max_frames 必须在 1–100000 之间")
    if not 0 < options.heartbeat_seconds <= 300:
        raise ProbeConfigurationError("heartbeat_seconds 必须在 0–300 之间")

    contract = RecipientContract.load(options.contract_path)
    probe_id = uuid.uuid4().hex
    output_dir = _prepare_output(options.output_dir)
    frames_dir = output_dir / "frames"
    targets_dir = output_dir / "target-payloads"
    frames_dir.mkdir()
    targets_dir.mkdir()
    metadata_path = output_dir / "frames.jsonl"
    target_events_path = output_dir / "target-events.jsonl"
    headers = _load_headers(options)
    started_at = _utc_now()
    deadline = asyncio.get_running_loop().time() + options.duration_seconds
    method_counts: Counter[str] = Counter()
    target_files: list[str] = []
    decoded_events: list[DecodedRecipientEvent] = []
    frame_count = binary_count = text_count = parse_error_count = 0
    message_count = target_count = target_failures = ack_count = 0
    heartbeat_counter = [0]
    transport_error = ""
    connected = False
    heartbeat_task: asyncio.Task[None] | None = None

    try:
        async with websockets.connect(
            options.websocket_url,
            origin=options.origin,
            additional_headers=headers,
            open_timeout=20,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
            max_size=MAX_MESSAGE_BYTES,
        ) as websocket:
            connected = True
            if options.send_application_heartbeat:
                heartbeat_task = asyncio.create_task(
                    _heartbeat_loop(websocket, options.heartbeat_seconds, heartbeat_counter),
                    name="douyin-p0-heartbeat",
                )
            with (
                metadata_path.open("a", encoding="utf-8", newline="\n") as metadata,
                target_events_path.open("a", encoding="utf-8", newline="\n") as target_events,
            ):
                while frame_count < options.max_frames:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                    except (TimeoutError, ConnectionClosed):
                        break
                    received_at_ms = int(time.time() * 1000)
                    received_monotonic_ns = time.monotonic_ns()
                    frame_count += 1
                    row: dict[str, object] = {
                        "sequence": frame_count,
                        "received_at_ms": received_at_ms,
                        "received_monotonic_ns": received_monotonic_ns,
                        "kind": "text" if isinstance(raw, str) else "binary",
                        "methods": [],
                    }
                    if isinstance(raw, str):
                        text_count += 1
                        encoded = raw.encode("utf-8")
                        row.update(
                            size_bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest()
                        )
                    else:
                        binary_count += 1
                        frame_path = frames_dir / f"{frame_count:06d}.bin"
                        frame_path.write_bytes(raw)
                        row.update(
                            size_bytes=len(raw),
                            sha256=hashlib.sha256(raw).hexdigest(),
                            raw_file=frame_path.relative_to(output_dir).as_posix(),
                        )
                        try:
                            inspected = inspect_frame(raw)
                            row["log_id"] = inspected.push.log_id
                            row["need_ack"] = inspected.response.need_ack
                            row["methods"] = [
                                item.method for item in inspected.response.messages if item.method
                            ]
                            message_count += len(inspected.response.messages)
                            for index, message in enumerate(inspected.response.messages, start=1):
                                method_counts[message.method or "<empty>"] += 1
                                if message.method != TARGET_METHOD:
                                    continue
                                target_count += 1
                                target_path = targets_dir / (
                                    f"{frame_count:06d}-{index:03d}-{_safe_token(message.msg_id)}.bin"
                                )
                                target_path.write_bytes(message.payload)
                                relative = target_path.relative_to(output_dir).as_posix()
                                target_files.append(relative)
                                try:
                                    event = decode_recipient_payload(
                                        message.payload,
                                        contract=contract,
                                        received_at_ms=received_at_ms,
                                        received_monotonic_ns=received_monotonic_ns,
                                        runtime_instance_id=probe_id,
                                        envelope_msg_id=message.msg_id,
                                    )
                                except RecipientDecodeError as exc:
                                    target_failures += 1
                                    target_events.write(
                                        json.dumps(
                                            {
                                                "ok": False,
                                                "payload_file": relative,
                                                "error": str(exc),
                                            },
                                            ensure_ascii=False,
                                            sort_keys=True,
                                        )
                                        + "\n"
                                    )
                                else:
                                    decoded_events.append(event)
                                    target_events.write(
                                        json.dumps(
                                            {
                                                "ok": True,
                                                "payload_file": relative,
                                                "event": event.to_dict(),
                                            },
                                            ensure_ascii=False,
                                            sort_keys=True,
                                        )
                                        + "\n"
                                    )
                            if (
                                options.send_ack
                                and inspected.response.need_ack
                                and inspected.push.log_id is not None
                                and inspected.response.internal_ext
                            ):
                                await websocket.send(
                                    build_ack(
                                        inspected.push.log_id, inspected.response.internal_ext
                                    )
                                )
                                ack_count += 1
                                row["ack_sent"] = True
                        except EnvelopeDecodeError as exc:
                            parse_error_count += 1
                            row["parse_error"] = str(exc)[:300]
                    metadata.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    metadata.flush()
                    target_events.flush()
    except (OSError, TimeoutError, ConnectionClosed, websockets.WebSocketException) as exc:
        transport_error = type(exc).__name__
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task

    report = ProbeReport(
        probe_id=probe_id,
        status="completed" if connected else "connection_failed",
        transport_connected=connected,
        transport_frames_received=frame_count > 0,
        transport_live_verified=(
            connected and frame_count > 0 and not options.allow_insecure_local
        ),
        synthetic_transport=options.allow_insecure_local,
        contract_live_verified=contract.live_verified,
        transport_error=transport_error,
        websocket_host=host,
        room_url=room_url,
        started_at=started_at,
        ended_at=_utc_now(),
        frame_count=frame_count,
        binary_frame_count=binary_count,
        text_frame_count=text_count,
        parse_error_count=parse_error_count,
        message_count=message_count,
        method_counts=dict(method_counts.most_common()),
        target_message_count=target_count,
        target_decode_success_count=len(decoded_events),
        target_decode_failure_count=target_failures,
        ack_sent_count=ack_count,
        heartbeat_sent_count=heartbeat_counter[0],
        contract_sha256=contract.sha256,
        field_report=summarize_recipient_events(decoded_events),
        target_payload_files=tuple(target_files),
        notes=(
            "transport_live_verified 仅在真实 wss:// 抖音主机收到现场帧时为 true。",
            "allow_insecure_local 只用于自动化测试，报告会标记 synthetic_transport=true。",
            "contract_live_verified=false 时不得声称目标字段契约完成现场确认。",
            "完整 WSS URL、Cookie 和请求头不会写入报告。",
            "私有 raw frame 和 payload 目录已由仓库 .gitignore 排除。",
        ),
    )
    (output_dir / "report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report

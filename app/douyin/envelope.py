from __future__ import annotations

import gzip
import io
from dataclasses import dataclass

from app.douyin.protobuf_wire import (
    ProtobufWireError,
    encode_bytes_field,
    encode_string_field,
    encode_varint_field,
    first_bytes,
    first_text,
    first_varint,
    parse_fields,
    repeated,
)

MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024


class EnvelopeDecodeError(ValueError):
    """Raised when a captured frame cannot be decoded by the P0 outer envelope parser."""


@dataclass(frozen=True, slots=True)
class EnvelopeMessage:
    method: str
    payload: bytes
    msg_id: str | None


@dataclass(frozen=True, slots=True)
class PushFrame:
    log_id: str | None
    payload_encoding: str
    payload_type: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class ResponseEnvelope:
    messages: tuple[EnvelopeMessage, ...]
    internal_ext: str
    heartbeat_duration_ms: int | None
    need_ack: bool


@dataclass(frozen=True, slots=True)
class InspectedFrame:
    push: PushFrame
    response: ResponseEnvelope


def _gzip_decompress_limited(data: bytes, limit: int = MAX_DECOMPRESSED_BYTES) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            output = stream.read(limit + 1)
    except OSError as exc:
        raise EnvelopeDecodeError(f"gzip payload 无法解压: {exc}") from exc
    if len(output) > limit:
        raise EnvelopeDecodeError(f"gzip 解压结果超过 {limit} 字节上限")
    return output


def parse_push_frame(data: bytes) -> PushFrame:
    try:
        fields = parse_fields(data)
        raw_log_id = first_varint(fields, 2)
        return PushFrame(
            log_id=str(raw_log_id) if raw_log_id is not None else None,
            payload_encoding=(first_text(fields, 6) or "").lower(),
            payload_type=first_text(fields, 7) or "",
            payload=first_bytes(fields, 8) or b"",
        )
    except ProtobufWireError as exc:
        raise EnvelopeDecodeError(f"PushFrame 解析失败: {exc}") from exc


def decode_push_payload(frame: PushFrame) -> bytes:
    encoding = frame.payload_encoding.strip().lower()
    if encoding in {"gzip", "x-gzip"} or frame.payload.startswith(b"\x1f\x8b"):
        return _gzip_decompress_limited(frame.payload)
    if encoding in {"", "none", "identity"}:
        return frame.payload
    raise EnvelopeDecodeError(f"暂不支持 payloadEncoding={frame.payload_encoding!r}")


def parse_response(data: bytes) -> ResponseEnvelope:
    try:
        fields = parse_fields(data)
        messages: list[EnvelopeMessage] = []
        for field in repeated(fields, 1):
            if not isinstance(field.value, bytes):
                raise ProtobufWireError("Response.messages 字段不是嵌套消息")
            message_fields = parse_fields(field.value)
            method = first_text(message_fields, 1) or ""
            payload = first_bytes(message_fields, 2) or b""
            raw_msg_id = first_varint(message_fields, 3)
            messages.append(
                EnvelopeMessage(
                    method=method,
                    payload=payload,
                    msg_id=str(raw_msg_id) if raw_msg_id is not None else None,
                )
            )
        return ResponseEnvelope(
            messages=tuple(messages),
            internal_ext=first_text(fields, 5) or "",
            heartbeat_duration_ms=first_varint(fields, 8),
            need_ack=bool(first_varint(fields, 9) or 0),
        )
    except ProtobufWireError as exc:
        raise EnvelopeDecodeError(f"Response 解析失败: {exc}") from exc


def inspect_frame(data: bytes) -> InspectedFrame:
    push = parse_push_frame(data)
    response = parse_response(decode_push_payload(push))
    return InspectedFrame(push=push, response=response)


def build_ack(log_id: str | None, internal_ext: str) -> bytes:
    """Build the best-effort outer ACK used only when the operator opts in."""

    output = bytearray()
    if log_id:
        try:
            output.extend(encode_varint_field(2, int(log_id)))
        except ValueError as exc:
            raise ValueError("log_id 必须是十进制字符串") from exc
    if not internal_ext:
        raise ValueError("internal_ext 不能为空")
    output.extend(encode_string_field(7, internal_ext))
    return bytes(output)


def build_heartbeat() -> bytes:
    return encode_string_field(7, "hb")


def build_message(method: str, payload: bytes, msg_id: int | None = None) -> bytes:
    data = encode_string_field(1, method) + encode_bytes_field(2, payload)
    if msg_id is not None:
        data += encode_varint_field(3, msg_id)
    return data


def build_response(
    messages: list[bytes],
    *,
    internal_ext: str = "",
    heartbeat_duration_ms: int = 10_000,
    need_ack: bool = False,
) -> bytes:
    data = b"".join(encode_bytes_field(1, message) for message in messages)
    if internal_ext:
        data += encode_string_field(5, internal_ext)
    data += encode_varint_field(8, heartbeat_duration_ms)
    if need_ack:
        data += encode_varint_field(9, 1)
    return data


def build_push_frame(
    response: bytes,
    *,
    log_id: int = 1,
    gzip_payload: bool = True,
    payload_type: str = "msg",
) -> bytes:
    payload = gzip.compress(response, mtime=0) if gzip_payload else response
    data = encode_varint_field(2, log_id)
    data += encode_string_field(6, "gzip" if gzip_payload else "none")
    data += encode_string_field(7, payload_type)
    data += encode_bytes_field(8, payload)
    return data

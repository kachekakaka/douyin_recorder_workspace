from __future__ import annotations

from dataclasses import dataclass

type WireValue = int | bytes
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
MAX_FIELDS = 100_000


class ProtobufWireError(ValueError):
    """Raised when a protobuf wire message is malformed, truncated, or unsupported."""


@dataclass(frozen=True, slots=True)
class WireField:
    number: int
    wire_type: int
    value: WireValue
    start: int
    end: int


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    value = 0
    shift = 0
    cursor = offset
    for _ in range(10):
        if cursor >= len(data):
            raise ProtobufWireError("varint 在消息结尾前被截断")
        byte = data[cursor]
        cursor += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, cursor
        shift += 7
    raise ProtobufWireError("varint 超过 10 字节")


def encode_varint(value: int) -> bytes:
    if value < 0:
        value &= (1 << 64) - 1
    output = bytearray()
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def parse_fields(
    data: bytes,
    *,
    max_length: int = MAX_MESSAGE_BYTES,
    max_fields: int = MAX_FIELDS,
) -> tuple[WireField, ...]:
    if len(data) > max_length:
        raise ProtobufWireError(f"protobuf 消息超过 {max_length} 字节上限")
    fields: list[WireField] = []
    cursor = 0
    while cursor < len(data):
        if len(fields) >= max_fields:
            raise ProtobufWireError(f"protobuf 字段数超过 {max_fields} 上限")
        start = cursor
        key, cursor = decode_varint(data, cursor)
        number = key >> 3
        wire_type = key & 0x07
        if number <= 0:
            raise ProtobufWireError("protobuf 字段号必须大于 0")
        if wire_type == 0:
            value, cursor = decode_varint(data, cursor)
        elif wire_type == 1:
            end = cursor + 8
            if end > len(data):
                raise ProtobufWireError("fixed64 字段被截断")
            value = data[cursor:end]
            cursor = end
        elif wire_type == 2:
            length, cursor = decode_varint(data, cursor)
            if length > max_length:
                raise ProtobufWireError(f"长度字段超过 {max_length} 字节上限")
            end = cursor + length
            if end > len(data):
                raise ProtobufWireError("length-delimited 字段被截断")
            value = data[cursor:end]
            cursor = end
        elif wire_type == 5:
            end = cursor + 4
            if end > len(data):
                raise ProtobufWireError("fixed32 字段被截断")
            value = data[cursor:end]
            cursor = end
        else:
            raise ProtobufWireError(f"P0 wire inspector 不支持 wire type {wire_type}")
        fields.append(WireField(number, wire_type, value, start, cursor))
    return tuple(fields)


def encode_key(field_number: int, wire_type: int) -> bytes:
    if field_number <= 0:
        raise ValueError("field_number 必须大于 0")
    if wire_type not in {0, 1, 2, 5}:
        raise ValueError("不支持的 wire type")
    return encode_varint((field_number << 3) | wire_type)


def encode_varint_field(field_number: int, value: int) -> bytes:
    return encode_key(field_number, 0) + encode_varint(value)


def encode_bytes_field(field_number: int, value: bytes) -> bytes:
    return encode_key(field_number, 2) + encode_varint(len(value)) + value


def encode_string_field(field_number: int, value: str) -> bytes:
    return encode_bytes_field(field_number, value.encode("utf-8"))


def repeated(fields: tuple[WireField, ...], number: int) -> tuple[WireField, ...]:
    return tuple(field for field in fields if field.number == number)


def first(fields: tuple[WireField, ...], number: int) -> WireField | None:
    return next((field for field in fields if field.number == number), None)


def first_varint(fields: tuple[WireField, ...], number: int) -> int | None:
    field = first(fields, number)
    if field is None:
        return None
    if field.wire_type != 0 or not isinstance(field.value, int):
        raise ProtobufWireError(f"字段 {number} 不是 varint")
    return field.value


def first_bytes(fields: tuple[WireField, ...], number: int) -> bytes | None:
    field = first(fields, number)
    if field is None:
        return None
    if field.wire_type != 2 or not isinstance(field.value, bytes):
        raise ProtobufWireError(f"字段 {number} 不是 length-delimited")
    return field.value


def first_text(fields: tuple[WireField, ...], number: int) -> str | None:
    value = first_bytes(fields, number)
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtobufWireError(f"字段 {number} 不是 UTF-8 字符串") from exc

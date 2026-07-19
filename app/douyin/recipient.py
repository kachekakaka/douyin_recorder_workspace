from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.douyin import TARGET_METHOD
from app.douyin.protobuf_wire import (
    ProtobufWireError,
    WireField,
    first_bytes,
    first_text,
    first_varint,
    parse_fields,
    repeated,
)


class RecipientDecodeError(ValueError):
    """Raised when a target payload does not match an explicitly supplied contract."""


@dataclass(frozen=True, slots=True)
class RecipientContract:
    contract_version: int
    target_method: str
    live_verified: bool
    status: str
    common_field: int
    recipient_user_id_field: int
    recipient_user_id_encoding: str
    change_reason_enum_field: int
    extra_map_field: int | None
    recipient_user_open_id_field: int | None
    common_msg_id_field: int
    common_create_time_field: int
    source_path: Path
    sha256: str
    notes: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> RecipientContract:
        try:
            raw_bytes = path.read_bytes()
        except FileNotFoundError as exc:
            raise RecipientDecodeError(f"缺少协议 contract: {path}") from exc
        try:
            raw = json.loads(raw_bytes)
        except json.JSONDecodeError as exc:
            raise RecipientDecodeError(f"协议 contract JSON 无效: {exc}") from exc
        if not isinstance(raw, dict):
            raise RecipientDecodeError("协议 contract 根节点必须是对象")
        target = raw.get("target")
        common = raw.get("common")
        if not isinstance(target, dict) or not isinstance(common, dict):
            raise RecipientDecodeError("协议 contract 缺少 target/common 对象")

        def positive(mapping: dict[str, Any], key: str) -> int:
            value = mapping.get(key)
            if not isinstance(value, int) or value <= 0:
                raise RecipientDecodeError(f"协议 contract 字段 {key} 必须是正整数")
            return value

        def optional_positive(mapping: dict[str, Any], key: str) -> int | None:
            value = mapping.get(key)
            if value is None:
                return None
            if not isinstance(value, int) or value <= 0:
                raise RecipientDecodeError(f"协议 contract 字段 {key} 必须为 null 或正整数")
            return value

        version = raw.get("contract_version")
        if not isinstance(version, int) or version <= 0:
            raise RecipientDecodeError("contract_version 必须是正整数")
        method = str(raw.get("target_method") or "")
        if method != TARGET_METHOD:
            raise RecipientDecodeError(f"P0 只允许目标 method: {TARGET_METHOD}")
        encoding = str(target.get("recipient_user_id_encoding") or "varint")
        if encoding not in {"varint", "string"}:
            raise RecipientDecodeError("recipient_user_id_encoding 只允许 varint/string")
        notes = raw.get("notes")
        return cls(
            contract_version=version,
            target_method=method,
            live_verified=bool(raw.get("live_verified", False)),
            status=str(raw.get("status") or "unknown"),
            common_field=positive(target, "common_field"),
            recipient_user_id_field=positive(target, "recipient_user_id_field"),
            recipient_user_id_encoding=encoding,
            change_reason_enum_field=positive(target, "change_reason_enum_field"),
            extra_map_field=optional_positive(target, "extra_map_field"),
            recipient_user_open_id_field=optional_positive(target, "recipient_user_open_id_field"),
            common_msg_id_field=positive(common, "msg_id_field"),
            common_create_time_field=positive(common, "create_time_field"),
            source_path=path.resolve(),
            sha256=hashlib.sha256(raw_bytes).hexdigest(),
            notes=tuple(str(item) for item in notes) if isinstance(notes, list) else (),
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "target_method": self.target_method,
            "live_verified": self.live_verified,
            "status": self.status,
            "sha256": self.sha256,
            "recipient_user_id_encoding": self.recipient_user_id_encoding,
            "recipient_user_open_id_field": self.recipient_user_open_id_field,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class DecodedRecipientEvent:
    method: str
    msg_id: str | None
    envelope_msg_id: str | None
    server_event_at_ms: int | None
    server_time_unit: str | None
    received_at_ms: int
    received_monotonic_ns: int
    runtime_instance_id: str
    recipient_user_id: str | None
    recipient_user_open_id: str | None
    recipient_key: str | None
    change_reason_enum: int | None
    extra: dict[str, str]
    payload_hash: str
    payload_size: int
    unknown_fields: tuple[dict[str, object], ...]
    dedup_key: str

    @property
    def delay_ms(self) -> int | None:
        if self.server_event_at_ms is None:
            return None
        return self.received_at_ms - self.server_event_at_ms

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["unknown_fields"] = list(self.unknown_fields)
        result["delay_ms"] = self.delay_ms
        return result


def canonical_recipient_key(
    recipient_user_id: str | None,
    recipient_user_open_id: str | None,
) -> str | None:
    user_id = (recipient_user_id or "").strip()
    if user_id and user_id != "0":
        return f"uid:{user_id}"
    open_id = (recipient_user_open_id or "").strip()
    if open_id:
        return f"openid:{open_id}"
    return None


def _normalize_server_time(value: int | None) -> tuple[int | None, str | None]:
    if value is None or value <= 0:
        return None, None
    if 1_000_000_000 <= value < 100_000_000_000:
        return value * 1000, "seconds-inferred"
    return value, "milliseconds-inferred"


def _decode_map_entry(data: bytes) -> tuple[str, str] | None:
    fields = parse_fields(data)
    key = first_text(fields, 1)
    value = first_text(fields, 2)
    if key is None or value is None:
        return None
    return key, value


def _read_user_id(fields: tuple[WireField, ...], contract: RecipientContract) -> str | None:
    if contract.recipient_user_id_encoding == "string":
        value = first_text(fields, contract.recipient_user_id_field)
        return value.strip() if value else None
    value = first_varint(fields, contract.recipient_user_id_field)
    return str(value) if value not in (None, 0) else None


def _unknown_summary(
    fields: tuple[WireField, ...], known: set[int]
) -> tuple[dict[str, object], ...]:
    output: list[dict[str, object]] = []
    for field in fields:
        if field.number in known:
            continue
        if isinstance(field.value, bytes):
            value: object = {
                "length": len(field.value),
                "sha256": hashlib.sha256(field.value).hexdigest(),
            }
        else:
            value = str(field.value)
        output.append({"field": field.number, "wire_type": field.wire_type, "value": value})
    return tuple(output)


def decode_recipient_payload(
    payload: bytes,
    *,
    contract: RecipientContract,
    received_at_ms: int,
    received_monotonic_ns: int,
    runtime_instance_id: str,
    envelope_msg_id: str | None = None,
) -> DecodedRecipientEvent:
    try:
        fields = parse_fields(payload)
        common_raw = first_bytes(fields, contract.common_field)
        common_fields = parse_fields(common_raw) if common_raw else ()
        common_msg_id_raw = first_varint(common_fields, contract.common_msg_id_field)
        common_create_time = first_varint(common_fields, contract.common_create_time_field)
        user_id = _read_user_id(fields, contract)
        open_id = None
        if contract.recipient_user_open_id_field is not None:
            open_id = first_text(fields, contract.recipient_user_open_id_field)
        change_reason = first_varint(fields, contract.change_reason_enum_field)
        extra: dict[str, str] = {}
        if contract.extra_map_field is not None:
            for field in repeated(fields, contract.extra_map_field):
                if isinstance(field.value, bytes):
                    entry = _decode_map_entry(field.value)
                    if entry is not None:
                        extra[entry[0]] = entry[1]
    except ProtobufWireError as exc:
        raise RecipientDecodeError(f"目标消息 payload 解析失败: {exc}") from exc

    msg_id = str(common_msg_id_raw) if common_msg_id_raw not in (None, 0) else envelope_msg_id
    normalized_time, inferred_unit = _normalize_server_time(common_create_time)
    payload_hash = hashlib.sha256(payload).hexdigest()
    bucket = received_at_ms // 1000
    dedup_key = f"msg:{msg_id}" if msg_id else f"hash:{payload_hash}:{bucket}"
    known = {
        contract.common_field,
        contract.recipient_user_id_field,
        contract.change_reason_enum_field,
    }
    if contract.extra_map_field is not None:
        known.add(contract.extra_map_field)
    if contract.recipient_user_open_id_field is not None:
        known.add(contract.recipient_user_open_id_field)
    return DecodedRecipientEvent(
        method=contract.target_method,
        msg_id=msg_id,
        envelope_msg_id=envelope_msg_id,
        server_event_at_ms=normalized_time,
        server_time_unit=inferred_unit,
        received_at_ms=received_at_ms,
        received_monotonic_ns=received_monotonic_ns,
        runtime_instance_id=runtime_instance_id,
        recipient_user_id=user_id,
        recipient_user_open_id=open_id,
        recipient_key=canonical_recipient_key(user_id, open_id),
        change_reason_enum=change_reason,
        extra=extra,
        payload_hash=payload_hash,
        payload_size=len(payload),
        unknown_fields=_unknown_summary(fields, known),
        dedup_key=dedup_key,
    )

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.douyin import TARGET_METHOD
from app.douyin.envelope import build_message, build_push_frame, build_response, inspect_frame
from app.douyin.protobuf_wire import (
    ProtobufWireError,
    encode_bytes_field,
    encode_string_field,
    encode_varint_field,
    parse_fields,
)
from app.douyin.recipient import (
    DecodedRecipientEvent,
    RecipientContract,
    canonical_recipient_key,
    decode_recipient_payload,
)
from app.douyin.timeline import RecipientTimelineReducer


def _contract(tmp_path: Path, *, string_id: bool = False, open_id_field: int | None = None):
    path = tmp_path / "contract.json"
    path.write_text(
        json.dumps(
            {
                "contract_version": 1,
                "target_method": TARGET_METHOD,
                "live_verified": False,
                "status": "synthetic-test",
                "target": {
                    "common_field": 1,
                    "recipient_user_id_field": 2,
                    "recipient_user_id_encoding": "string" if string_id else "varint",
                    "change_reason_enum_field": 3,
                    "extra_map_field": 4,
                    "recipient_user_open_id_field": open_id_field,
                },
                "common": {"msg_id_field": 2, "create_time_field": 4},
                "notes": ["synthetic unit test"],
            }
        ),
        encoding="utf-8",
    )
    return RecipientContract.load(path)


def _event(
    *,
    dedup: str,
    at_ms: int,
    monotonic_ns: int,
    recipient_id: str | None,
    runtime: str = "runtime-a",
) -> DecodedRecipientEvent:
    return DecodedRecipientEvent(
        method=TARGET_METHOD,
        msg_id=dedup,
        envelope_msg_id=dedup,
        server_event_at_ms=at_ms - 5,
        server_time_unit="fixture",
        received_at_ms=at_ms,
        received_monotonic_ns=monotonic_ns,
        runtime_instance_id=runtime,
        recipient_user_id=recipient_id,
        recipient_user_open_id=None,
        recipient_key=canonical_recipient_key(recipient_id, None),
        change_reason_enum=2,
        extra={},
        payload_hash=dedup.rjust(64, "0")[-64:],
        payload_size=0,
        unknown_fields=(),
        dedup_key=f"msg:{dedup}",
    )


def test_envelope_round_trip() -> None:
    target_payload = encode_varint_field(2, 123)
    message = build_message(TARGET_METHOD, target_payload, msg_id=99)
    response = build_response([message], internal_ext="cursor", need_ack=True)
    inspected = inspect_frame(build_push_frame(response, log_id=7, gzip_payload=True))

    assert inspected.push.log_id == "7"
    assert inspected.response.need_ack is True
    assert inspected.response.internal_ext == "cursor"
    assert inspected.response.messages[0].method == TARGET_METHOD
    assert inspected.response.messages[0].msg_id == "99"
    assert inspected.response.messages[0].payload == target_payload


def test_recipient_decode_preserves_64_bit_id_as_string(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    common = encode_varint_field(2, 9001) + encode_varint_field(4, 1_700_000_000)
    extra = encode_string_field(1, "scene") + encode_string_field(2, "group-live")
    payload = (
        encode_bytes_field(1, common)
        + encode_varint_field(2, 73_504_089_679)
        + encode_varint_field(3, 2)
        + encode_bytes_field(4, extra)
    )

    event = decode_recipient_payload(
        payload,
        contract=contract,
        received_at_ms=1_700_000_000_123,
        received_monotonic_ns=123_456,
        runtime_instance_id="runtime-a",
        envelope_msg_id="fallback",
    )

    assert event.msg_id == "9001"
    assert event.recipient_user_id == "73504089679"
    assert event.recipient_key == "uid:73504089679"
    assert event.change_reason_enum == 2
    assert event.extra == {"scene": "group-live"}
    assert event.server_event_at_ms == 1_700_000_000_000
    assert event.delay_ms == 123


def test_open_id_is_only_fallback_when_user_id_is_empty(tmp_path: Path) -> None:
    contract = _contract(tmp_path, string_id=True, open_id_field=5)
    payload = (
        encode_string_field(2, "0")
        + encode_varint_field(3, 1)
        + encode_string_field(5, "open-id-value")
    )
    event = decode_recipient_payload(
        payload,
        contract=contract,
        received_at_ms=1000,
        received_monotonic_ns=100,
        runtime_instance_id="runtime-a",
    )
    assert event.recipient_key == "openid:open-id-value"


def test_timeline_uses_strict_unknown_after_disconnect() -> None:
    reducer = RecipientTimelineReducer()
    reducer.start_session(at_ms=1000, monotonic_ns=10, runtime_instance_id="runtime-a")
    first = _event(dedup="1", at_ms=1100, monotonic_ns=20, recipient_id="1")
    assert reducer.apply_event(first) == "active"
    duplicate = _event(dedup="1", at_ms=1150, monotonic_ns=25, recipient_id="1")
    assert reducer.apply_event(duplicate) == "duplicate"
    same = _event(dedup="2", at_ms=1200, monotonic_ns=30, recipient_id="1")
    assert reducer.apply_event(same) == "same-recipient"

    reducer.im_disconnected(at_ms=1300, monotonic_ns=40, runtime_instance_id="runtime-a")
    reducer.im_reconnected()
    assert reducer.current is not None
    assert reducer.current.status == "unknown"
    assert reducer.current.reason == "im_disconnected"
    assert reducer.current.recipient_key is None

    reducer.apply_event(_event(dedup="3", at_ms=1400, monotonic_ns=50, recipient_id="2"))
    assert reducer.current is not None
    assert reducer.current.status == "active"
    assert reducer.current.recipient_key == "uid:2"
    assert reducer.events["msg:1"].duplicate_count == 1


def test_cross_runtime_close_does_not_compare_monotonic_values() -> None:
    reducer = RecipientTimelineReducer()
    reducer.start_session(at_ms=1000, monotonic_ns=999, runtime_instance_id="runtime-a")
    reducer.im_disconnected(at_ms=1100, monotonic_ns=1, runtime_instance_id="runtime-b")
    assert reducer.intervals[0].ended_monotonic_ns is None
    assert reducer.intervals[0].ended_runtime_instance_id == "runtime-b"


def test_wire_parser_rejects_truncated_varint() -> None:
    with pytest.raises(ProtobufWireError):
        parse_fields(b"\x08\x80")

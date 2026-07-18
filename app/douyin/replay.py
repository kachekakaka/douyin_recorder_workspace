from __future__ import annotations

import base64
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.douyin import TARGET_METHOD
from app.douyin.envelope import EnvelopeDecodeError, inspect_frame
from app.douyin.recipient import (
    DecodedRecipientEvent,
    RecipientContract,
    RecipientDecodeError,
    canonical_recipient_key,
    decode_recipient_payload,
)
from app.douyin.timeline import RecipientTimelineReducer


class ReplayError(ValueError):
    """Raised when a replay fixture is malformed or violates strict expected semantics."""


@dataclass(frozen=True, slots=True)
class ReplayResult:
    scenario: str
    fixture_synthetic: bool
    fixture_live_verified: bool
    methods: dict[str, int]
    target_messages: int
    target_decode_failures: int
    reducer_snapshot: dict[str, object]
    report: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "fixture_synthetic": self.fixture_synthetic,
            "fixture_live_verified": self.fixture_live_verified,
            "methods": self.methods,
            "target_messages": self.target_messages,
            "target_decode_failures": self.target_decode_failures,
            "reducer": self.reducer_snapshot,
            "report": self.report,
        }


def _required_int(step: dict[str, Any], key: str) -> int:
    value = step.get(key)
    if not isinstance(value, int):
        raise ReplayError(f"step.{key} 必须是整数")
    return value


def _required_text(step: dict[str, Any], key: str) -> str:
    value = step.get(key)
    if not isinstance(value, str) or not value:
        raise ReplayError(f"step.{key} 必须是非空字符串")
    return value


def _decode_base64(value: Any, label: str) -> bytes:
    if not isinstance(value, str):
        raise ReplayError(f"{label} 必须是 base64 字符串")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ReplayError(f"{label} 不是有效 base64") from exc


def _percentile_nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(percentile * len(ordered) + 0.999999))
    return ordered[min(rank - 1, len(ordered) - 1)]


def summarize_recipient_events(events: list[DecodedRecipientEvent]) -> dict[str, object]:
    delays = [event.delay_ms for event in events if event.delay_ms is not None]
    change_reasons: dict[str, int] = {}
    server_time_units: dict[str, int] = {}
    user_ids: set[str] = set()
    open_ids: set[str] = set()
    empty_count = 0
    unknown_field_numbers: set[int] = set()
    for event in events:
        if event.change_reason_enum is not None:
            key = str(event.change_reason_enum)
            change_reasons[key] = change_reasons.get(key, 0) + 1
        if event.server_time_unit:
            server_time_units[event.server_time_unit] = (
                server_time_units.get(event.server_time_unit, 0) + 1
            )
        if event.recipient_user_id:
            user_ids.add(event.recipient_user_id)
        if event.recipient_user_open_id:
            open_ids.add(event.recipient_user_open_id)
        if event.recipient_key is None:
            empty_count += 1
        for item in event.unknown_fields:
            field = item.get("field")
            if isinstance(field, int):
                unknown_field_numbers.add(field)
    return {
        "event_count": len(events),
        "empty_recipient_count": empty_count,
        "recipient_user_ids": sorted(user_ids),
        "recipient_user_open_ids": sorted(open_ids),
        "change_reason_distribution": change_reasons,
        "server_time_unit_distribution": server_time_units,
        "unknown_target_field_numbers": sorted(unknown_field_numbers),
        "server_delay_ms": {
            "count": len(delays),
            "min": min(delays) if delays else None,
            "median": statistics.median(delays) if delays else None,
            "p95": _percentile_nearest_rank(delays, 0.95),
            "max": max(delays) if delays else None,
        },
    }


def _build_report(
    decoded_events: list[DecodedRecipientEvent],
    reducer: RecipientTimelineReducer,
    steps: list[dict[str, Any]],
) -> dict[str, object]:
    canonical = [item.event for item in reducer.events.values()]
    report = summarize_recipient_events(canonical)
    report.update(
        event_count=len(decoded_events),
        unique_event_count=len(reducer.events),
        duplicate_frame_count=sum(item.duplicate_count for item in reducer.events.values()),
        late_event_count=sum(1 for item in reducer.events.values() if item.is_late),
        transport_disconnects=sum(1 for step in steps if step.get("type") == "im_disconnected"),
        transport_reconnects=sum(1 for step in steps if step.get("type") == "im_reconnected"),
    )
    return report


def run_fixture(path: Path, *, contract: RecipientContract) -> ReplayResult:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReplayError(f"找不到 replay fixture: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReplayError(f"replay fixture JSON 无效: {exc}") from exc
    if not isinstance(document, dict):
        raise ReplayError("fixture 根节点必须是对象")
    steps = document.get("steps")
    if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
        raise ReplayError("fixture.steps 必须是对象数组")

    reducer = RecipientTimelineReducer()
    methods: dict[str, int] = {}
    decoded_events: list[DecodedRecipientEvent] = []
    target_messages = 0
    decode_failures = 0

    for raw_step in steps:
        step = dict(raw_step)
        step_type = _required_text(step, "type")
        if step_type == "session_started":
            reducer.start_session(
                at_ms=_required_int(step, "at_ms"),
                monotonic_ns=_required_int(step, "monotonic_ns"),
                runtime_instance_id=_required_text(step, "runtime_instance_id"),
            )
            continue
        if step_type == "frame":
            frame = _decode_base64(step.get("frame_base64"), "frame_base64")
            received_at_ms = _required_int(step, "received_at_ms")
            monotonic_ns = _required_int(step, "received_monotonic_ns")
            runtime_instance_id = _required_text(step, "runtime_instance_id")
            try:
                inspected = inspect_frame(frame)
            except EnvelopeDecodeError as exc:
                raise ReplayError(str(exc)) from exc
            for message in inspected.response.messages:
                methods[message.method] = methods.get(message.method, 0) + 1
                if message.method != TARGET_METHOD:
                    continue
                target_messages += 1
                try:
                    event = decode_recipient_payload(
                        message.payload,
                        contract=contract,
                        received_at_ms=received_at_ms,
                        received_monotonic_ns=monotonic_ns,
                        runtime_instance_id=runtime_instance_id,
                        envelope_msg_id=message.msg_id,
                    )
                except RecipientDecodeError:
                    decode_failures += 1
                    continue
                decoded_events.append(event)
                reducer.apply_event(event)
            continue
        if step_type == "normalized_recipient":
            received_at_ms = _required_int(step, "received_at_ms")
            user_id = step.get("recipient_user_id")
            open_id = step.get("recipient_user_open_id")
            if user_id is not None and not isinstance(user_id, str):
                raise ReplayError("recipient_user_id 必须为字符串或 null")
            if open_id is not None and not isinstance(open_id, str):
                raise ReplayError("recipient_user_open_id 必须为字符串或 null")
            raw_msg_id = step.get("msg_id")
            if raw_msg_id is not None and not isinstance(raw_msg_id, str):
                raise ReplayError("msg_id 必须为字符串或 null")
            reason = step.get("change_reason_enum")
            if reason is not None and not isinstance(reason, int):
                raise ReplayError("change_reason_enum 必须为整数或 null")
            server_event_at_ms = step.get("server_event_at_ms")
            if server_event_at_ms is not None and not isinstance(server_event_at_ms, int):
                raise ReplayError("server_event_at_ms 必须为整数或 null")
            payload_hash = _required_text(step, "payload_hash")
            event = DecodedRecipientEvent(
                method=TARGET_METHOD,
                msg_id=raw_msg_id,
                envelope_msg_id=raw_msg_id,
                server_event_at_ms=server_event_at_ms,
                server_time_unit="fixture-normalized",
                received_at_ms=received_at_ms,
                received_monotonic_ns=_required_int(step, "received_monotonic_ns"),
                runtime_instance_id=_required_text(step, "runtime_instance_id"),
                recipient_user_id=user_id,
                recipient_user_open_id=open_id,
                recipient_key=canonical_recipient_key(user_id, open_id),
                change_reason_enum=reason,
                extra={},
                payload_hash=payload_hash,
                payload_size=0,
                unknown_fields=(),
                dedup_key=(
                    f"msg:{raw_msg_id}"
                    if raw_msg_id
                    else f"hash:{payload_hash}:{received_at_ms // 1000}"
                ),
            )
            methods[TARGET_METHOD] = methods.get(TARGET_METHOD, 0) + 1
            target_messages += 1
            decoded_events.append(event)
            reducer.apply_event(event)
            continue
        if step_type == "im_disconnected":
            reducer.im_disconnected(
                at_ms=_required_int(step, "at_ms"),
                monotonic_ns=_required_int(step, "monotonic_ns"),
                runtime_instance_id=_required_text(step, "runtime_instance_id"),
            )
            continue
        if step_type == "im_reconnected":
            reducer.im_reconnected()
            continue
        if step_type == "session_ended":
            reducer.end_session(
                at_ms=_required_int(step, "at_ms"),
                monotonic_ns=_required_int(step, "monotonic_ns"),
                runtime_instance_id=_required_text(step, "runtime_instance_id"),
            )
            continue
        raise ReplayError(f"不支持的 step.type: {step_type}")

    snapshot = reducer.snapshot()
    report = _build_report(decoded_events, reducer, steps)
    expected = document.get("expected")
    if isinstance(expected, dict):
        _assert_expected(
            expected,
            snapshot=snapshot,
            report=report,
            target_messages=target_messages,
            decode_failures=decode_failures,
        )
    return ReplayResult(
        scenario=str(document.get("name") or path.stem),
        fixture_synthetic=bool(document.get("synthetic", False)),
        fixture_live_verified=bool(document.get("live_verified", False)),
        methods=methods,
        target_messages=target_messages,
        target_decode_failures=decode_failures,
        reducer_snapshot=snapshot,
        report=report,
    )


def _assert_expected(
    expected: dict[str, Any],
    *,
    snapshot: dict[str, object],
    report: dict[str, object],
    target_messages: int,
    decode_failures: int,
) -> None:
    if "target_messages" in expected and target_messages != expected["target_messages"]:
        raise ReplayError(
            f"target_messages 期望 {expected['target_messages']}，实际 {target_messages}"
        )
    if "decode_failures" in expected and decode_failures != expected["decode_failures"]:
        raise ReplayError(
            f"decode_failures 期望 {expected['decode_failures']}，实际 {decode_failures}"
        )
    expected_intervals = expected.get("intervals")
    if isinstance(expected_intervals, list):
        actual = snapshot.get("intervals")
        if not isinstance(actual, list):
            raise ReplayError("reducer 未返回 intervals")
        compact_actual = [
            {
                "status": item.get("status"),
                "reason": item.get("reason"),
                "recipient_key": item.get("recipient_key"),
                "started_at_ms": item.get("started_at_ms"),
                "ended_at_ms": item.get("ended_at_ms"),
            }
            for item in actual
            if isinstance(item, dict)
        ]
        if compact_actual != expected_intervals:
            detail = {"expected": expected_intervals, "actual": compact_actual}
            raise ReplayError(
                "intervals 与 fixture 期望不一致:\n"
                + json.dumps(detail, ensure_ascii=False, indent=2)
            )
    expected_report = expected.get("report")
    if isinstance(expected_report, dict):
        for key, value in expected_report.items():
            if report.get(key) != value:
                raise ReplayError(f"report.{key} 期望 {value!r}，实际 {report.get(key)!r}")


def render_markdown(result: ReplayResult, contract: RecipientContract) -> str:
    report = result.report
    delay = report.get("server_delay_ms", {})
    lines = [
        f"# P0 协议回放报告：{result.scenario}",
        "",
        f"- Fixture 为合成数据：`{str(result.fixture_synthetic).lower()}`",
        f"- Fixture 现场验证：`{str(result.fixture_live_verified).lower()}`",
        f"- Contract 现场验证：`{str(contract.live_verified).lower()}`",
        f"- Contract SHA-256：`{contract.sha256}`",
        f"- 目标 method：`{TARGET_METHOD}`",
        f"- 目标消息数：{result.target_messages}",
        f"- 解码失败数：{result.target_decode_failures}",
        f"- 去重后事件数：{report.get('unique_event_count')}",
        f"- 重复帧数：{report.get('duplicate_frame_count')}",
        f"- 迟到事件数：{report.get('late_event_count')}",
        f"- 空 recipient 数：{report.get('empty_recipient_count')}",
        f"- IM 断线/重连：{report.get('transport_disconnects')}/"
        f"{report.get('transport_reconnects')}",
        "",
        "## ID 与 change_reason",
        "",
        f"- user_id：`{json.dumps(report.get('recipient_user_ids'), ensure_ascii=False)}`",
        f"- open_id：`{json.dumps(report.get('recipient_user_open_ids'), ensure_ascii=False)}`",
        f"- change_reason：`{json.dumps(report.get('change_reason_distribution'))}`",
        f"- 未知目标字段号：`{json.dumps(report.get('unknown_target_field_numbers'))}`",
        "",
        "## common.create_time 到本机 receive_time 延迟",
        "",
        f"- 样本：{delay.get('count') if isinstance(delay, dict) else None}",
        f"- min/median/p95/max：{delay.get('min') if isinstance(delay, dict) else None} / "
        f"{delay.get('median') if isinstance(delay, dict) else None} / "
        f"{delay.get('p95') if isinstance(delay, dict) else None} / "
        f"{delay.get('max') if isinstance(delay, dict) else None} ms",
        "",
        "> 此报告只证明代码可重复处理合成 fixture；",
        "> `live_verified=false` 时不得声称目标房间已经现场验证。",
        "",
    ]
    return "\n".join(lines)

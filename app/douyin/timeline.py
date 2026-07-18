from __future__ import annotations

from dataclasses import asdict, dataclass

from app.douyin.recipient import DecodedRecipientEvent


@dataclass(slots=True)
class TimelineInterval:
    status: str
    reason: str | None
    recipient_key: str | None
    recipient_user_id: str | None
    recipient_user_open_id: str | None
    started_at_ms: int
    ended_at_ms: int | None
    started_monotonic_ns: int
    ended_monotonic_ns: int | None
    runtime_instance_id: str
    ended_runtime_instance_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class CanonicalEvent:
    event: DecodedRecipientEvent
    duplicate_count: int = 0
    last_received_at_ms: int = 0
    is_late: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            **self.event.to_dict(),
            "duplicate_count": self.duplicate_count,
            "last_received_at_ms": self.last_received_at_ms,
            "is_late": self.is_late,
        }


class RecipientTimelineReducer:
    """Deterministic P0 reducer with no network or database side effects."""

    def __init__(self) -> None:
        self.intervals: list[TimelineInterval] = []
        self.events: dict[str, CanonicalEvent] = {}
        self.connected = False
        self.ended = False

    @property
    def current(self) -> TimelineInterval | None:
        if not self.intervals:
            return None
        interval = self.intervals[-1]
        return interval if interval.ended_at_ms is None else None

    def start_session(self, *, at_ms: int, monotonic_ns: int, runtime_instance_id: str) -> None:
        if self.intervals:
            raise ValueError("场次已经开始")
        if at_ms < 0 or monotonic_ns < 0 or not runtime_instance_id:
            raise ValueError("场次起始时间与 runtime_instance_id 无效")
        self.connected = True
        self._open(
            status="waiting",
            reason="waiting_first_event",
            recipient_key=None,
            recipient_user_id=None,
            recipient_user_open_id=None,
            at_ms=at_ms,
            monotonic_ns=monotonic_ns,
            runtime_instance_id=runtime_instance_id,
        )

    def apply_event(self, event: DecodedRecipientEvent) -> str:
        if self.ended:
            raise ValueError("场次已经结束")
        current = self.current
        if current is None:
            raise ValueError("场次尚未开始")
        existing = self.events.get(event.dedup_key)
        if existing is not None:
            existing.duplicate_count += 1
            existing.last_received_at_ms = max(existing.last_received_at_ms, event.received_at_ms)
            return "duplicate"

        is_late = event.received_at_ms < current.started_at_ms
        self.events[event.dedup_key] = CanonicalEvent(
            event=event,
            last_received_at_ms=event.received_at_ms,
            is_late=is_late,
        )
        if is_late:
            return "late"

        if event.recipient_key is None:
            if current.status == "unknown" and current.reason == "empty_recipient":
                return "same-unknown"
            self._transition(
                status="unknown",
                reason="empty_recipient",
                recipient_key=None,
                recipient_user_id=None,
                recipient_user_open_id=None,
                at_ms=event.received_at_ms,
                monotonic_ns=event.received_monotonic_ns,
                runtime_instance_id=event.runtime_instance_id,
            )
            return "unknown"

        if current.status == "active" and current.recipient_key == event.recipient_key:
            return "same-recipient"
        self._transition(
            status="active",
            reason="recipient_event_received",
            recipient_key=event.recipient_key,
            recipient_user_id=event.recipient_user_id,
            recipient_user_open_id=event.recipient_user_open_id,
            at_ms=event.received_at_ms,
            monotonic_ns=event.received_monotonic_ns,
            runtime_instance_id=event.runtime_instance_id,
        )
        return "active"

    def im_disconnected(self, *, at_ms: int, monotonic_ns: int, runtime_instance_id: str) -> None:
        if self.ended:
            raise ValueError("场次已经结束")
        self.connected = False
        current = self.current
        if current is None:
            raise ValueError("场次尚未开始")
        if current.status == "unknown" and current.reason == "im_disconnected":
            return
        self._transition(
            status="unknown",
            reason="im_disconnected",
            recipient_key=None,
            recipient_user_id=None,
            recipient_user_open_id=None,
            at_ms=at_ms,
            monotonic_ns=monotonic_ns,
            runtime_instance_id=runtime_instance_id,
        )

    def im_reconnected(self) -> None:
        if self.ended:
            raise ValueError("场次已经结束")
        # Transport recovery isn't recipient evidence. The current Unknown interval remains open.
        self.connected = True

    def end_session(
        self,
        *,
        at_ms: int,
        monotonic_ns: int,
        runtime_instance_id: str,
    ) -> None:
        current = self.current
        if current is None:
            raise ValueError("场次尚未开始或已结束")
        self._close(
            current,
            at_ms=at_ms,
            monotonic_ns=monotonic_ns,
            runtime_instance_id=runtime_instance_id,
        )
        self.ended = True
        self.connected = False

    def _transition(
        self,
        *,
        status: str,
        reason: str | None,
        recipient_key: str | None,
        recipient_user_id: str | None,
        recipient_user_open_id: str | None,
        at_ms: int,
        monotonic_ns: int,
        runtime_instance_id: str,
    ) -> None:
        current = self.current
        if current is None:
            raise ValueError("没有可闭合的当前区间")
        if at_ms < current.started_at_ms:
            raise ValueError("新区间不能早于当前区间")
        self._close(
            current,
            at_ms=at_ms,
            monotonic_ns=monotonic_ns,
            runtime_instance_id=runtime_instance_id,
        )
        self._open(
            status=status,
            reason=reason,
            recipient_key=recipient_key,
            recipient_user_id=recipient_user_id,
            recipient_user_open_id=recipient_user_open_id,
            at_ms=at_ms,
            monotonic_ns=monotonic_ns,
            runtime_instance_id=runtime_instance_id,
        )

    def _open(
        self,
        *,
        status: str,
        reason: str | None,
        recipient_key: str | None,
        recipient_user_id: str | None,
        recipient_user_open_id: str | None,
        at_ms: int,
        monotonic_ns: int,
        runtime_instance_id: str,
    ) -> None:
        self.intervals.append(
            TimelineInterval(
                status=status,
                reason=reason,
                recipient_key=recipient_key,
                recipient_user_id=recipient_user_id,
                recipient_user_open_id=recipient_user_open_id,
                started_at_ms=at_ms,
                ended_at_ms=None,
                started_monotonic_ns=monotonic_ns,
                ended_monotonic_ns=None,
                runtime_instance_id=runtime_instance_id,
            )
        )

    @staticmethod
    def _close(
        interval: TimelineInterval,
        *,
        at_ms: int,
        monotonic_ns: int,
        runtime_instance_id: str,
    ) -> None:
        if at_ms < interval.started_at_ms:
            raise ValueError("区间结束时间不能早于开始时间")
        if (
            runtime_instance_id == interval.runtime_instance_id
            and monotonic_ns < interval.started_monotonic_ns
        ):
            raise ValueError("同一 runtime_instance 内单调时钟不能倒退")
        interval.ended_at_ms = at_ms
        interval.ended_runtime_instance_id = runtime_instance_id
        interval.ended_monotonic_ns = (
            monotonic_ns if runtime_instance_id == interval.runtime_instance_id else None
        )

    def snapshot(self) -> dict[str, object]:
        current = self.current
        return {
            "connected": self.connected,
            "ended": self.ended,
            "current": current.to_dict() if current else None,
            "intervals": [interval.to_dict() for interval in self.intervals],
            "events": [event.to_dict() for event in self.events.values()],
        }

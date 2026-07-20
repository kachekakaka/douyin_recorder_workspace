from __future__ import annotations

import asyncio

from app.douyin import TARGET_METHOD
from app.douyin.recipient import DecodedRecipientEvent, RecipientContract
from app.sessions.models import RecipientProjectionResult, RecipientSessionState
from app.sessions.repository import RecipientSessionRepository, RecipientSessionStateError


class RecipientSessionService:
    """Serialize recipient transitions per session while SQLite serializes all writes."""

    def __init__(
        self,
        repository: RecipientSessionRepository,
        contract: RecipientContract,
    ) -> None:
        self.repository = repository
        self.contract = contract
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def start_session(self, **kwargs: object) -> RecipientSessionState:
        kwargs.setdefault("protocol_contract_sha256", self.contract.sha256)
        kwargs.setdefault("protocol_live_verified", self.contract.live_verified)
        session_id = str(kwargs.get("session_id") or "")
        async with await self._lock(session_id):
            return await self.repository.start_session(**kwargs)  # type: ignore[arg-type]

    async def apply_event(
        self,
        *,
        session_id: str,
        event: DecodedRecipientEvent,
        raw_payload_json: str,
    ) -> RecipientProjectionResult:
        if event.method != TARGET_METHOD or event.method != self.contract.target_method:
            raise RecipientSessionStateError("非目标 recipient method 不得改变状态")
        async with await self._lock(session_id):
            return await self.repository.apply_event(
                session_id=session_id,
                event=event,
                raw_payload_json=raw_payload_json,
            )

    async def im_disconnected(self, **kwargs: object) -> RecipientSessionState:
        session_id = str(kwargs.get("session_id") or "")
        async with await self._lock(session_id):
            return await self.repository.im_disconnected(**kwargs)  # type: ignore[arg-type]

    async def im_reconnected(self, *, session_id: str) -> RecipientSessionState:
        async with await self._lock(session_id):
            return await self.repository.im_reconnected(session_id=session_id)

    async def end_session(self, **kwargs: object) -> dict[str, object]:
        session_id = str(kwargs.get("session_id") or "")
        async with await self._lock(session_id):
            return await self.repository.end_session(**kwargs)  # type: ignore[arg-type]

    async def _lock(self, session_id: str) -> asyncio.Lock:
        if not session_id:
            raise RecipientSessionStateError("session_id 必填")
        async with self._locks_guard:
            return self._locks.setdefault(session_id, asyncio.Lock())

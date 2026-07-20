from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from app.rooms import ROOM_KEY_PATTERN, RoomNotFoundError
from app.sessions import RecipientSessionNotFoundError

router = APIRouter(prefix="/api/rooms", tags=["recipient"])
RoomKeyPath = Annotated[
    str,
    Path(min_length=2, max_length=64, pattern=ROOM_KEY_PATTERN),
]
SessionIdQuery = Annotated[
    str | None,
    Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"),
]
PageLimit = Annotated[int, Query(ge=1, le=500)]
PageOffset = Annotated[int, Query(ge=0, le=1_000_000)]


def _state(request: Request):
    return request.app.state.app_state


async def _ensure_room(request: Request, room_key: str) -> None:
    try:
        await _state(request).room_service.get_room(room_key)
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="直播间不存在") from exc


@router.get("/{room_key}/recipient-state")
async def get_recipient_state(
    room_key: RoomKeyPath,
    request: Request,
) -> dict[str, object]:
    await _ensure_room(request, room_key)
    state = await _state(request).recipient_repository.get_state(room_key)
    return {"ok": True, "data": state.to_public_dict()}


@router.get("/{room_key}/recipient-events")
async def list_recipient_events(
    room_key: RoomKeyPath,
    request: Request,
    session_id: SessionIdQuery = None,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
) -> dict[str, object]:
    await _ensure_room(request, room_key)
    try:
        events = await _state(request).recipient_repository.list_events(
            room_key=room_key,
            session_id=session_id,
            limit=limit,
            offset=offset,
        )
    except RecipientSessionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="recipient session 不存在或不属于该直播间",
        ) from exc
    return {
        "ok": True,
        "data": {
            "items": [event.to_public_dict() for event in events],
            "count": len(events),
            "limit": limit,
            "offset": offset,
        },
    }


@router.get("/{room_key}/recipient-intervals")
async def list_recipient_intervals(
    room_key: RoomKeyPath,
    request: Request,
    session_id: SessionIdQuery = None,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
) -> dict[str, object]:
    await _ensure_room(request, room_key)
    try:
        intervals = await _state(request).recipient_repository.list_intervals(
            room_key=room_key,
            session_id=session_id,
            limit=limit,
            offset=offset,
        )
    except RecipientSessionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="recipient session 不存在或不属于该直播间",
        ) from exc
    return {
        "ok": True,
        "data": {
            "items": [interval.to_public_dict() for interval in intervals],
            "count": len(intervals),
            "limit": limit,
            "offset": offset,
        },
    }

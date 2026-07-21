from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from app.recording import (
    RecordingAlreadyActiveError,
    RecordingCandidateUnavailableError,
    RecordingRoomDisabledError,
    RecordingSessionError,
)
from app.rooms import ROOM_KEY_PATTERN, RoomNotFoundError

router = APIRouter(prefix="/api/rooms", tags=["recording"])
RoomKeyPath = Annotated[
    str,
    Path(min_length=2, max_length=64, pattern=ROOM_KEY_PATTERN),
]
SessionIdQuery = Annotated[
    str | None,
    Query(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9._-]+$"),
]
PageLimit = Annotated[int, Query(ge=1, le=500)]
PageOffset = Annotated[int, Query(ge=0, le=1_000_000)]


def _service(request: Request):
    return request.app.state.app_state.recording_service


@router.post("/{room_key}/actions/start-recording")
async def start_recording(room_key: RoomKeyPath, request: Request) -> dict[str, object]:
    try:
        state = await _service(request).start_recording(room_key)
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="直播间不存在") from exc
    except RecordingAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail="直播间已有活动录制") from exc
    except RecordingRoomDisabledError as exc:
        raise HTTPException(status_code=409, detail="直播间已禁用") from exc
    except RecordingCandidateUnavailableError as exc:
        raise HTTPException(status_code=409, detail="当前没有可用的安全流候选") from exc
    except RecordingSessionError as exc:
        raise HTTPException(status_code=503, detail="录制进程启动失败") from exc
    return {"ok": True, "data": state.to_public_dict()}


@router.post("/{room_key}/actions/stop-recording")
async def stop_recording(room_key: RoomKeyPath, request: Request) -> dict[str, object]:
    try:
        state = await _service(request).stop_recording(room_key)
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="直播间不存在") from exc
    except RecordingSessionError as exc:
        raise HTTPException(status_code=503, detail="录制进程停止失败") from exc
    return {"ok": True, "data": state.to_public_dict()}


@router.get("/{room_key}/recording")
async def get_recording(room_key: RoomKeyPath, request: Request) -> dict[str, object]:
    try:
        state = await _service(request).get_state(room_key)
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="直播间不存在") from exc
    return {"ok": True, "data": state.to_public_dict()}


@router.get("/{room_key}/recording/sessions")
async def list_recording_sessions(
    room_key: RoomKeyPath,
    request: Request,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
) -> dict[str, object]:
    try:
        items = await _service(request).list_sessions(
            room_key=room_key,
            limit=limit,
            offset=offset,
        )
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="直播间不存在") from exc
    return {
        "ok": True,
        "data": {"items": [item.to_public_dict() for item in items], "total": len(items)},
    }


@router.get("/{room_key}/recording/segments")
async def list_recording_segments(
    room_key: RoomKeyPath,
    request: Request,
    session_id: SessionIdQuery = None,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
) -> dict[str, object]:
    try:
        items = await _service(request).list_segments(
            room_key=room_key,
            session_id=session_id,
            limit=limit,
            offset=offset,
        )
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="直播间不存在") from exc
    except RecordingSessionError as exc:
        raise HTTPException(
            status_code=404,
            detail="recording session 不存在或不属于该直播间",
        ) from exc
    return {
        "ok": True,
        "data": {"items": [item.to_public_dict() for item in items], "total": len(items)},
    }

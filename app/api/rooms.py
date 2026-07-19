from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.rooms import (
    RoomAlreadyExistsError,
    RoomCreate,
    RoomNotFoundError,
    RoomPatch,
)

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


def _service(request: Request):
    return request.app.state.app_state.room_service


@router.get("")
async def list_rooms(request: Request) -> dict[str, object]:
    rooms = await _service(request).list_rooms()
    return {
        "ok": True,
        "data": {
            "items": [room.model_dump(mode="json") for room in rooms],
            "total": len(rooms),
        },
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_room(payload: RoomCreate, request: Request) -> dict[str, object]:
    try:
        room = await _service(request).create_room(payload)
    except RoomAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail="room_key 或 room_url 已存在") from exc
    return {"ok": True, "data": room.model_dump(mode="json")}


@router.patch("/{room_key}")
async def update_room(room_key: str, payload: RoomPatch, request: Request) -> dict[str, object]:
    try:
        room = await _service(request).update_room(room_key, payload)
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="直播间不存在") from exc
    except RoomAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail="room_url 已由其他 room_key 使用") from exc
    return {"ok": True, "data": room.model_dump(mode="json")}


@router.post("/{room_key}/actions/check")
async def check_room(room_key: str, request: Request) -> dict[str, object]:
    try:
        result = await _service(request).check_room(room_key)
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="直播间不存在") from exc
    return {"ok": True, "data": result.snapshot.to_public_dict()}


@router.post("/{room_key}/actions/enable")
async def enable_room(room_key: str, request: Request) -> dict[str, object]:
    try:
        room = await _service(request).set_enabled(room_key, True)
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="直播间不存在") from exc
    return {"ok": True, "data": room.model_dump(mode="json")}


@router.post("/{room_key}/actions/disable")
async def disable_room(room_key: str, request: Request) -> dict[str, object]:
    try:
        room = await _service(request).set_enabled(room_key, False)
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="直播间不存在") from exc
    return {"ok": True, "data": room.model_dump(mode="json")}

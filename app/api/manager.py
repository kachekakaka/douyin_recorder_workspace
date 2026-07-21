from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request

from app.rooms import ROOM_KEY_PATTERN, RoomNotFoundError

router = APIRouter(tags=["manager"])
RoomKeyPath = Annotated[
    str,
    Path(min_length=2, max_length=64, pattern=ROOM_KEY_PATTERN),
]


def _state(request: Request):
    return request.app.state.app_state


@router.get("/api/manager/status")
async def get_manager_status(request: Request) -> dict[str, object]:
    status = await _state(request).room_manager.get_status()
    return {"ok": True, "data": status.to_public_dict()}


@router.post("/api/manager/actions/reconcile")
async def reconcile_manager(request: Request) -> dict[str, object]:
    status = await _state(request).room_manager.reconcile()
    return {"ok": True, "data": status.to_public_dict()}


@router.get("/api/rooms/{room_key}/worker")
async def get_room_worker(
    room_key: RoomKeyPath,
    request: Request,
) -> dict[str, object]:
    try:
        worker = await _state(request).room_manager.get_worker(room_key)
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="直播间不存在") from exc
    return {"ok": True, "data": worker.to_public_dict()}

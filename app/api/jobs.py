from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from app.postprocess import (
    ExportPlanError,
    PostprocessJobNotFoundError,
    PostprocessJobStateError,
)

router = APIRouter(prefix="/api", tags=["postprocess"])
SessionIdPath = Annotated[
    str,
    Path(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9._-]+$"),
]
JobIdPath = Annotated[
    str,
    Path(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$"),
]
JobStatusQuery = Annotated[
    Literal["queued", "running", "succeeded", "failed", "canceled"] | None,
    Query(alias="status"),
]
PageLimit = Annotated[int, Query(ge=1, le=500)]
PageOffset = Annotated[int, Query(ge=0, le=1_000_000)]


def _service(request: Request):
    return request.app.state.app_state.postprocess_service


@router.post(
    "/recording/sessions/{session_id}/actions/create-export",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_export(session_id: SessionIdPath, request: Request) -> dict[str, object]:
    try:
        job = await _service(request).create_export(session_id)
    except ExportPlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "data": job.to_public_dict()}


@router.get("/jobs")
async def list_jobs(
    request: Request,
    status_filter: JobStatusQuery = None,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
) -> dict[str, object]:
    items = await _service(request).list_jobs(
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return {
        "ok": True,
        "data": {"items": [item.to_public_dict() for item in items], "total": len(items)},
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: JobIdPath, request: Request) -> dict[str, object]:
    try:
        job = await _service(request).get_job(job_id)
    except PostprocessJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="postprocess job 不存在") from exc
    return {"ok": True, "data": job.to_public_dict()}


@router.post("/jobs/{job_id}/actions/retry")
async def retry_job(job_id: JobIdPath, request: Request) -> dict[str, object]:
    try:
        job = await _service(request).retry_job(job_id)
    except PostprocessJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="postprocess job 不存在") from exc
    except PostprocessJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "data": job.to_public_dict()}


@router.post("/jobs/{job_id}/actions/cancel")
async def cancel_job(job_id: JobIdPath, request: Request) -> dict[str, object]:
    try:
        job = await _service(request).cancel_job(job_id)
    except PostprocessJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="postprocess job 不存在") from exc
    except PostprocessJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "data": job.to_public_dict()}

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import manager_router, recipient_router, recording_router, rooms_router
from app.paths import ROOT
from app.security import validate_request_boundary
from app.settings import Settings
from app.state import AppState

WEB_DIR = ROOT / "web"


def create_app(*, settings: Settings | None = None, state: AppState | None = None) -> FastAPI:
    app_state = state or AppState.create(settings or Settings.load())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await app_state.start()
        try:
            yield
        finally:
            await app_state.stop()

    app = FastAPI(
        title="douyin_recorder_workspace",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.app_state = app_state
    app.include_router(rooms_router)
    app.include_router(manager_router)
    app.include_router(recipient_router)
    app.include_router(recording_router)

    @app.middleware("http")
    async def security_boundary_and_headers(request: Request, call_next):
        violation = validate_request_boundary(
            request,
            configured_host=app_state.settings.host,
            configured_port=app_state.settings.port,
        )
        if violation is None:
            response = await call_next(request)
        else:
            response = JSONResponse(
                {"ok": False, "error": violation.message, "code": violation.code},
                status_code=violation.status_code,
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        if request.url.path.startswith("/api/") or request.url.path in {"/healthz", "/readyz"}:
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "ok": True,
            "version": __version__,
            "runtime_instance_id": app_state.runtime_instance_id,
        }

    @app.get("/readyz")
    async def readyz(refresh: bool = Query(default=False)):
        data = await app_state.readiness(refresh=refresh)
        status_code = 200 if data["ready"] else 503
        return JSONResponse({"ok": bool(data["ready"]), "data": data}, status_code=status_code)

    @app.get("/api/status")
    async def api_status(refresh: bool = Query(default=False)) -> dict[str, object]:
        data = await app_state.readiness(refresh=refresh)
        return {
            "ok": True,
            "data": {
                "version": __version__,
                "phase": "P2A",
                "loopback_only": True,
                "authentication_implemented": False,
                "protocol_live_verified": app_state.protocol_contract.live_verified,
                "limitations": [
                    "目标推荐收礼人消息仍未形成经审查的现场 fixture",
                    "P2A 提供单进程多房间自动检查、自动录制与故障隔离",
                    "真实 IM 自动接入、后处理导出与公网管理尚未启用",
                ],
                **data,
            },
        }

    if WEB_DIR.is_dir():
        assets = WEB_DIR / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})

    return app


def run() -> None:
    import uvicorn

    settings = Settings.load()
    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level="info",
    )

from app.api.jobs import router as jobs_router
from app.api.manager import router as manager_router
from app.api.recipient import router as recipient_router
from app.api.recording import router as recording_router
from app.api.rooms import router as rooms_router

__all__ = ["jobs_router", "manager_router", "recipient_router", "recording_router", "rooms_router"]

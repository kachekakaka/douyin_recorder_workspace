from __future__ import annotations

from app.db import Database
from app.sessions._read_store import RecipientSessionReadStore
from app.sessions._write_store import RecipientSessionWriteStore
from app.sessions.repository_errors import (
    RecipientSessionError,
    RecipientSessionNotFoundError,
    RecipientSessionStateError,
)


class RecipientSessionRepository(RecipientSessionReadStore, RecipientSessionWriteStore):
    def __init__(self, database: Database) -> None:
        self.database = database


__all__ = [
    "RecipientSessionError",
    "RecipientSessionNotFoundError",
    "RecipientSessionRepository",
    "RecipientSessionStateError",
]

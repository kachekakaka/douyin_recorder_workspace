from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.douyin.live_page import LivePageError, normalize_room_reference

_ROOM_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
Quality = Literal["origin", "uhd", "hd", "sd", "ld", "md"]
Protocol = Literal["flv", "hls"]


class RoomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    room_key: Annotated[str, Field(min_length=2, max_length=64)]
    room_url: Annotated[str, Field(min_length=3, max_length=500)]
    enabled: bool = True
    quality: Quality = "origin"
    protocol: Protocol = "flv"
    poll_interval_seconds: Annotated[int, Field(ge=5, le=3600)] = 15

    @field_validator("room_key")
    @classmethod
    def validate_room_key(cls, value: str) -> str:
        normalized = value.casefold()
        if not _ROOM_KEY_RE.fullmatch(normalized):
            raise ValueError("room_key 只允许 2–64 位小写字母、数字、下划线或横线")
        return normalized

    @field_validator("room_url")
    @classmethod
    def validate_room_url(cls, value: str) -> str:
        try:
            return normalize_room_reference(value).room_url
        except LivePageError as exc:
            raise ValueError(str(exc)) from exc


class RoomPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    room_url: Annotated[str | None, Field(min_length=3, max_length=500)] = None
    enabled: bool | None = None
    quality: Quality | None = None
    protocol: Protocol | None = None
    poll_interval_seconds: Annotated[int | None, Field(ge=5, le=3600)] = None

    @field_validator("room_url")
    @classmethod
    def validate_room_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalize_room_reference(value).room_url
        except LivePageError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def require_change(self) -> RoomPatch:
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        null_fields = sorted(
            name for name in self.model_fields_set if getattr(self, name, None) is None
        )
        if null_fields:
            raise ValueError(f"修改字段不得为 null: {', '.join(null_fields)}")
        return self


class RoomRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_key: str
    room_url: str
    enabled: bool
    quality: Quality
    protocol: Protocol
    poll_interval_seconds: int
    created_at_ms: int
    updated_at_ms: int
    latest_check: dict[str, object] | None = None


class RoomListResponse(BaseModel):
    items: list[RoomRecord]
    total: int

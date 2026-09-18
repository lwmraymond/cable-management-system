"""Cable retirement contracts; previews are advisory, never authorization tokens."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from app.models import CableStatus


class CableRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A removal reason is required")
        return value.strip()


class LifecycleBlocker(BaseModel):
    code: str
    message: str


class CableEndpointPreview(BaseModel):
    side: str
    device_name: str | None
    port_label: str | None


class CableDeletionPreview(BaseModel):
    id: uuid.UUID
    identifier: str
    version: int
    action: Literal["delete", "remove"]
    status: CableStatus
    allowed: bool
    blockers: list[LifecycleBlocker]
    endpoints: list[CableEndpointPreview]
    route_segment_count: int


class CableLifecycleResult(BaseModel):
    id: uuid.UUID
    action: Literal["delete", "remove"]
    version: int

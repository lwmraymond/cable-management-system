"""Strict editing payloads for the spatial workspace; positions use metres."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


FinitePosition = Annotated[float, Field(ge=0, le=100000, allow_inf_nan=False)]
Rotation = Annotated[float, Field(ge=-3600, le=3600, allow_inf_nan=False)]


class ScenePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RoomEntrance(ScenePayload):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    wall: Literal["north", "south", "east", "west"]
    offset_m: float = Field(ge=0, allow_inf_nan=False)
    width_m: float = Field(gt=0, allow_inf_nan=False)
    height_m: float = Field(gt=0, allow_inf_nan=False)


class RoomCreate(ScenePayload):
    parent_id: uuid.UUID | None = None
    identifier: str = Field(min_length=3, max_length=180)
    name: str = Field(min_length=1, max_length=180)
    kind: Literal["server_room", "room"] = "server_room"
    width_m: float = Field(gt=0, le=10000, allow_inf_nan=False)
    depth_m: float = Field(gt=0, le=10000, allow_inf_nan=False)
    height_m: float = Field(gt=0, le=1000, allow_inf_nan=False)
    entrances: list[RoomEntrance] | None = Field(default=None, min_length=1, max_length=20)


class RackGridCreate(ScenePayload):
    location_id: uuid.UUID
    identifier_prefix: str = Field(min_length=1, max_length=170)
    name_prefix: str = Field(min_length=1, max_length=170)
    count: int = Field(default=1, ge=1, le=12, strict=True)
    columns: int = Field(default=1, ge=1, le=6, strict=True)
    position_x: FinitePosition
    position_y: FinitePosition
    rotation: Rotation = 0
    gap_m: float = Field(default=0.6, ge=0, le=100, allow_inf_nan=False)
    width_mm: int = Field(default=600, ge=500, le=3000, strict=True)
    depth_mm: int = Field(default=1000, ge=450, le=3000, strict=True)
    height_u: int = Field(default=42, ge=1, le=60, strict=True)


class RackPoseUpdate(ScenePayload):
    expected_version: int = Field(ge=1, strict=True)
    position_x: FinitePosition
    position_y: FinitePosition
    position_z: FinitePosition = 0
    rotation: Rotation = 0


class CablePolicyUpdate(ScenePayload):
    expected_version: int = Field(ge=1, strict=True)
    allows_cables: bool = Field(strict=True)
    allowed_media: list[Literal["copper", "fiber"]] = Field(max_length=2)

    @model_validator(mode="after")
    def valid_media(self):
        if len(set(self.allowed_media)) != len(self.allowed_media):
            raise ValueError("allowed_media must not contain duplicates")
        if self.allows_cables and not self.allowed_media:
            raise ValueError("An enabled cable policy needs at least one allowed medium")
        return self


class RoomEntrancesUpdate(ScenePayload):
    expected_version: int = Field(ge=1, strict=True)
    entrances: list[RoomEntrance] = Field(min_length=1, max_length=20)


class RoutePreview(ScenePayload):
    port_a_id: uuid.UUID
    port_b_id: uuid.UUID
    media_type: str = Field(min_length=1, max_length=80)
    excluded_pathway_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    route_segment_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)

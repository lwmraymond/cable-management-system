"""Advanced Fiber, pair/channel, breakout, OTDR and generic trace API."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.api.fiber import commit_operation
from app.security import Principal
from app.services.fiber_advanced import FiberAdvancedService
from app.services.topology_trace import TopologyTraceService


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VersionPayload(StrictPayload):
    expected_version: int = Field(ge=1, strict=True)


class TerminationCreate(StrictPayload):
    strand_id: uuid.UUID
    side: Literal["A", "B"]
    port_id: uuid.UUID
    connection_type: Literal["connector", "pigtail", "fusion", "mechanical"] = "connector"
    loss_db: float = Field(default=0.0, ge=0, le=10, allow_inf_nan=False, strict=True)


class ChannelMemberCreate(StrictPayload):
    kind: Literal["fiber_strand", "copper_pair"]
    resource_id: uuid.UUID
    role: str = Field(default="member", min_length=1, max_length=80)


class ChannelCreate(StrictPayload):
    project_id: uuid.UUID
    identifier: str = Field(min_length=1, max_length=180)
    name: str = Field(min_length=1, max_length=180)
    medium: Literal["fiber", "copper"]
    topology: Literal["simplex", "duplex", "quad", "bundle", "ethernet"]
    status: Literal["planned", "active", "reserved", "retired"] = "planned"
    members: list[ChannelMemberCreate] = Field(min_length=1, max_length=600)


class BreakoutLegCreate(StrictPayload):
    parent_strand_id: uuid.UUID
    parent_side: Literal["A", "B"]
    child_strand_id: uuid.UUID
    child_side: Literal["A", "B"]
    label: str | None = Field(default=None, min_length=1, max_length=180)
    loss_db: float = Field(default=0.0, ge=0, le=10, allow_inf_nan=False, strict=True)


class BreakoutCreate(StrictPayload):
    project_id: uuid.UUID
    device_id: uuid.UUID
    identifier: str = Field(min_length=1, max_length=180)
    name: str = Field(min_length=1, max_length=180)
    mode: Literal["fanout", "fanin", "passive"]
    legs: list[BreakoutLegCreate] = Field(min_length=1, max_length=576)


class OtdrEventCreate(StrictPayload):
    event_type: Literal["launch", "connector", "splice", "bend", "reflective", "end", "unknown"]
    distance_m: float = Field(ge=0, le=1_000_000, allow_inf_nan=False, strict=True)
    loss_db: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False, strict=True)
    reflectance_db: float | None = Field(
        default=None, ge=-120, le=20, allow_inf_nan=False, strict=True
    )
    confidence: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False, strict=True)
    notes: str | None = Field(default=None, max_length=1000)


class OtdrRecordCreate(StrictPayload):
    project_id: uuid.UUID
    cable_id: uuid.UUID
    strand_id: uuid.UUID | None = None
    direction: Literal["A", "B"]
    wavelength_nm: int = Field(ge=600, le=1700, strict=True)
    acquired_at: datetime
    source_name: str = Field(min_length=1, max_length=500)
    source_object_key: str | None = Field(default=None, max_length=500)
    total_length_m: float | None = Field(
        default=None, ge=0, le=1_000_000, allow_inf_nan=False, strict=True
    )
    end_to_end_loss_db: float | None = Field(
        default=None, ge=0, le=100, allow_inf_nan=False, strict=True
    )
    metadata: dict = Field(default_factory=dict)
    events: list[OtdrEventCreate] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def acquired_at_must_be_timezone_aware(self) -> "OtdrRecordCreate":
        if self.acquired_at.tzinfo is None or self.acquired_at.utcoffset() is None:
            raise ValueError("acquired_at must include a timezone")
        return self


class OtdrEventLink(VersionPayload):
    linked_kind: Literal["splice", "fiber_termination", "breakout_leg"]
    linked_id: uuid.UUID
    link_offset_m: float | None = Field(
        default=None,
        ge=-1_000_000,
        le=1_000_000,
        allow_inf_nan=False,
        strict=True,
    )


def build_fiber_advanced_router(get_db: Callable, get_principal: Callable) -> APIRouter:
    router = APIRouter(prefix="/fiber", tags=["fiber-advanced"])

    @router.post("/terminations", status_code=201)
    def create_termination(
        body: TerminationCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FiberAdvancedService(db, principal).terminate_strand,
            **body.model_dump(),
        )

    @router.post("/terminations/{termination_id}/release")
    def release_termination(
        termination_id: uuid.UUID,
        body: VersionPayload,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FiberAdvancedService(db, principal).release_termination,
            termination_id=termination_id,
            expected_version=body.expected_version,
        )

    @router.post("/cables/{cable_id}/pairs/provision", status_code=201)
    def provision_pairs(
        cable_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FiberAdvancedService(db, principal).provision_pairs,
            cable_id=cable_id,
        )

    @router.get("/cables/{cable_id}/pairs")
    def get_pairs(
        cable_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return FiberAdvancedService(db, principal).get_pairs(cable_id)

    @router.post("/channels", status_code=201)
    def create_channel(
        body: ChannelCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        payload = body.model_dump()
        return commit_operation(
            db,
            FiberAdvancedService(db, principal).create_channel,
            **payload,
        )

    @router.get("/channels/{channel_id}")
    def get_channel(
        channel_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return FiberAdvancedService(db, principal).get_channel(channel_id)

    @router.post("/channels/{channel_id}/release")
    def release_channel(
        channel_id: uuid.UUID,
        body: VersionPayload,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FiberAdvancedService(db, principal).release_channel,
            channel_id=channel_id,
            expected_version=body.expected_version,
        )

    @router.get("/channels/{channel_id}/trace")
    def trace_channel(
        channel_id: uuid.UUID,
        max_nodes: Annotated[int, Query(ge=2, le=1000)] = 500,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return TopologyTraceService(db, principal).trace_channel(
            channel_id,
            max_nodes=max_nodes,
        )

    @router.post("/breakouts", status_code=201)
    def create_breakout(
        body: BreakoutCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FiberAdvancedService(db, principal).create_breakout,
            **body.model_dump(exclude_none=True),
        )

    @router.get("/breakouts/{breakout_id}")
    def get_breakout(
        breakout_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return FiberAdvancedService(db, principal).get_breakout(breakout_id)

    @router.post("/breakouts/{breakout_id}/release")
    def release_breakout(
        breakout_id: uuid.UUID,
        body: VersionPayload,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FiberAdvancedService(db, principal).release_breakout,
            breakout_id=breakout_id,
            expected_version=body.expected_version,
        )

    @router.post("/otdr-records", status_code=201)
    def create_otdr_record(
        body: OtdrRecordCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FiberAdvancedService(db, principal).create_otdr_record,
            **body.model_dump(),
        )

    @router.get("/otdr-records/{record_id}")
    def get_otdr_record(
        record_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return FiberAdvancedService(db, principal).get_otdr_record(record_id)

    @router.post("/otdr-events/{event_id}/link")
    def link_otdr_event(
        event_id: uuid.UUID,
        body: OtdrEventLink,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FiberAdvancedService(db, principal).link_otdr_event,
            event_id=event_id,
            **body.model_dump(),
        )

    @router.get("/cables/{cable_id}/trace")
    def trace_cable(
        cable_id: uuid.UUID,
        strand_number: Annotated[int | None, Query(ge=1, le=576)] = None,
        pair_number: Annotated[int | None, Query(ge=1, le=600)] = None,
        max_nodes: Annotated[int, Query(ge=2, le=1000)] = 500,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return TopologyTraceService(db, principal).trace_cable(
            cable_id,
            strand_number=strand_number,
            pair_number=pair_number,
            max_nodes=max_nodes,
        )

    return router

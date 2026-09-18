from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import CableStatus, LocationType, WorkOrderStatus


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class LocationCreate(APIModel):
    location_type: LocationType
    identifier: str = Field(min_length=3, max_length=180)
    name: str = Field(min_length=1, max_length=180)
    parent_id: uuid.UUID | None = None
    dimensions: dict[str, Any] = Field(default_factory=dict)
    coordinates: dict[str, Any] = Field(default_factory=dict)
    transform_3d: dict[str, Any] = Field(default_factory=dict)
    floor_plan_reference: str | None = None


class LocationRead(APIModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    parent_id: uuid.UUID | None
    location_type: LocationType
    identifier: str
    name: str
    dimensions: dict[str, Any]
    coordinates: dict[str, Any]
    transform_3d: dict[str, Any]
    version: int


class RackCreate(APIModel):
    location_id: uuid.UUID
    rack_identifier: str = Field(min_length=3, max_length=180)
    name: str
    height_u: int = Field(default=42, ge=1, le=60)
    width_mm: int = Field(default=600, ge=100)
    depth_mm: int = Field(default=1000, ge=100)
    position_x: float = 0
    position_y: float = 0
    position_z: float = 0
    rotation: float = 0
    reserved_units: list[int] = Field(default_factory=list)


class RackRead(APIModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    location_id: uuid.UUID
    rack_identifier: str
    name: str
    height_u: int
    width_mm: int
    depth_mm: int
    position_x: float
    position_y: float
    position_z: float
    rotation: float
    reserved_units: list[int]
    version: int


class DeviceTemplateCreate(APIModel):
    manufacturer: str
    model: str
    device_type: str
    rack_units: int = Field(ge=1, le=20)
    width_mm: int = 482
    depth_mm: int = 350
    height_mm: int | None = None
    port_blueprint: list[dict[str, Any]] = Field(default_factory=list)
    model_3d_reference: str | None = None


class DeviceTemplateRead(APIModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    manufacturer: str
    model: str
    device_type: str
    rack_units: int
    width_mm: int
    depth_mm: int
    port_blueprint: list[dict[str, Any]]


class DeviceCreate(APIModel):
    rack_id: uuid.UUID
    template_id: uuid.UUID
    identifier: str
    name: str
    start_u: int = Field(ge=1, le=60)
    face: str = "front"


class DeviceRead(APIModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    rack_id: uuid.UUID | None
    location_id: uuid.UUID
    template_id: uuid.UUID | None
    identifier: str
    name: str
    device_type: str
    rack_units: int
    start_u: int
    face: str
    version: int


class PortRead(APIModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    identifier: str
    label: str
    connector_type: str
    media_type: str
    direction: str
    front_or_rear: str
    position_index: int
    position: dict[str, Any]
    status: str


class SegmentCreate(APIModel):
    sequence: int | None = None
    name: str
    length_m: float = 0
    capacity_area_mm2: float | None = None
    reserved_percent: float = 0
    coordinates: list[dict[str, float]] = Field(default_factory=list)


class PathwayCreate(APIModel):
    location_id: uuid.UUID
    identifier: str
    name: str
    pathway_type: str
    capacity_area_mm2: float | None = None
    segments: list[SegmentCreate] = Field(default_factory=list)


class RoutePortion(APIModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    segment_id: uuid.UUID
    start_offset_m: float = Field(ge=0, le=1000000)
    end_offset_m: float = Field(ge=0, le=1000000)
    geometry_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class CableCreate(APIModel):
    identifier: str
    media_type: str
    construction: str
    port_a_id: uuid.UUID
    port_b_id: uuid.UUID
    project_id: uuid.UUID | None = None
    color: str | None = None
    length_m: float | None = None
    route_segment_ids: list[uuid.UUID] = Field(default_factory=list)
    route_portions: list[RoutePortion] | None = Field(default=None, max_length=100)


class CableRead(APIModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID | None
    identifier: str
    media_type: str
    construction: str
    color: str | None
    length_m: float | None
    strand_count: int | None
    installation_status: CableStatus
    installer_id: uuid.UUID | None
    installed_at: datetime | None
    tested_at: datetime | None
    test_status: str | None
    version: int


class WorkOrderCreate(APIModel):
    project_id: uuid.UUID
    location_id: uuid.UUID | None = None
    cable_id: uuid.UUID | None = None
    work_order_number: str
    title: str
    description: str = ""
    assigned_organization_id: uuid.UUID
    assigned_user_id: uuid.UUID | None = None
    due_at: datetime | None = None


class WorkOrderRead(APIModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    location_id: uuid.UUID | None
    cable_id: uuid.UUID | None
    work_order_number: str
    title: str
    description: str
    assigned_organization_id: uuid.UUID
    assigned_user_id: uuid.UUID | None
    status: WorkOrderStatus
    due_at: datetime | None
    version: int


class TestSubmit(APIModel):
    result: str
    measurements: dict[str, Any] = Field(default_factory=dict)
    work_order_id: uuid.UUID | None = None
    attachment_name: str | None = None


class TestRecordRead(APIModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    cable_id: uuid.UUID
    work_order_id: uuid.UUID | None
    tester_id: uuid.UUID
    result: str
    measurements: dict[str, Any]
    tested_at: datetime
    attachment_name: str | None
    status: str
    approved_by: uuid.UUID | None
    approved_at: datetime | None


class AccessGrantCreate(APIModel):
    subject_organization_id: uuid.UUID
    subject_user_id: uuid.UUID | None = None
    project_id: uuid.UUID
    location_id: uuid.UUID | None = None
    permissions: list[str]
    starts_at: datetime | None = None
    expires_at: datetime | None = None

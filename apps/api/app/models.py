from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TenantOwnedMixin(UUIDMixin, TimestampMixin):
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizationType(enum.StrEnum):
    CUSTOMER = "customer"
    CONTRACTOR = "contractor"
    PARTNER = "partner"
    INTERNAL = "internal"


class LocationType(enum.StrEnum):
    REGION = "region"
    CAMPUS = "campus"
    SITE = "site"
    BUILDING = "building"
    FLOOR = "floor"
    ZONE = "zone"
    ROOM = "room"
    TR = "tr"
    ER = "er"
    MDF = "mdf"
    MMR = "mmr"
    DATA_HALL = "data_hall"
    ENTRANCE_FACILITY = "entrance_facility"
    ROW = "row"
    OTHER = "other"


class CableStatus(enum.StrEnum):
    PLANNED = "planned"
    APPROVED = "approved"
    ORDERED = "ordered"
    STAGED = "staged"
    INSTALLED = "installed"
    TERMINATED = "terminated"
    TESTED = "tested"
    COMMISSIONED = "commissioned"
    IN_SERVICE = "in_service"
    SPARE = "spare"
    ABANDONED = "abandoned"
    REMOVED = "removed"


class WorkOrderStatus(enum.StrEnum):
    DRAFT = "draft"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    AWAITING_TEST = "awaiting_test"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AccessGrantStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class Organization(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    organization_type: Mapped[OrganizationType] = mapped_column(
        SAEnum(OrganizationType, native_enum=False), nullable=False
    )
    external_reference: Mapped[str | None] = mapped_column(String(180))


class UserIdentity(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("email", name="uq_user_identity_email"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(180), nullable=False)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Tenant(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenant_slug"),
        CheckConstraint("workspace_kind IN ('personal', 'shared')", name="ck_workspace_kind"),
        CheckConstraint(
            "workspace_kind != 'personal' OR workspace_owner_id IS NOT NULL",
            name="ck_personal_workspace_owner",
        ),
    )
    owner_organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    workspace_kind: Mapped[str] = mapped_column(
        String(20), default="shared", server_default="shared", nullable=False
    )
    workspace_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_identities.id", ondelete="RESTRICT", name="fk_workspace_owner"),
    )
    compliance_mode: Mapped[str] = mapped_column(String(20), default="assisted", nullable=False)
    active_standard_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("standard_profiles.id"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StandardProfile(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "standard_profiles"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "standard_family", "edition", "name", name="uq_standard_profile_scope"
        ),
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "tenants.id",
            ondelete="CASCADE",
            use_alter=True,
            name="fk_standard_profiles_tenant_id_tenants",
        ),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    standard_family: Mapped[str] = mapped_column(String(50), nullable=False)
    edition: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    rules_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    identifier_templates: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    validation_rules: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    label_templates: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    required_records: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class TenantMembership(Base, TenantOwnedMixin):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership_user"),)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_identities.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Project(Base, TenantOwnedMixin):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_number", name="uq_project_number_tenant"),
    )
    project_number: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    customer_organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    contractor_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id")
    )
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Location(Base, TenantOwnedMixin):
    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "identifier", name="uq_location_identifier_tenant"),
        Index("ix_location_tenant_parent", "tenant_id", "parent_id"),
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    location_type: Mapped[LocationType] = mapped_column(
        SAEnum(LocationType, native_enum=False), nullable=False
    )
    identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    coordinates: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    transform_3d: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    floor_plan_reference: Mapped[str | None] = mapped_column(String(500))


class AccessGrant(Base, TenantOwnedMixin):
    __tablename__ = "access_grants"
    __table_args__ = (Index("ix_access_grant_subject", "tenant_id", "subject_user_id", "status"),)
    subject_organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_identities.id")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE")
    )
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    approved_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_identities.id"), nullable=False
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[AccessGrantStatus] = mapped_column(
        SAEnum(AccessGrantStatus, native_enum=False),
        default=AccessGrantStatus.PENDING,
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))


class Rack(Base, TenantOwnedMixin):
    __tablename__ = "racks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "rack_identifier", name="uq_rack_identifier_tenant"),
        CheckConstraint("height_u >= 1 AND height_u <= 60", name="ck_rack_height"),
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rack_identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    role: Mapped[str | None] = mapped_column(String(80))
    manufacturer: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(120))
    height_u: Mapped[int] = mapped_column(Integer, default=42, nullable=False)
    width_mm: Mapped[int] = mapped_column(Integer, default=600, nullable=False)
    depth_mm: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    max_weight_kg: Mapped[float | None] = mapped_column(Float)
    position_x: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    position_y: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    position_z: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    rotation: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    front_direction: Mapped[str] = mapped_column(String(20), default="north", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    reserved_units: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)


class DeviceTemplate(Base, TenantOwnedMixin):
    __tablename__ = "device_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "manufacturer", "model", name="uq_device_template_model"),
    )
    manufacturer: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    device_type: Mapped[str] = mapped_column(String(80), nullable=False)
    rack_units: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    width_mm: Mapped[int] = mapped_column(Integer, default=482, nullable=False)
    depth_mm: Mapped[int] = mapped_column(Integer, default=350, nullable=False)
    height_mm: Mapped[int | None] = mapped_column(Integer)
    port_blueprint: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    model_3d_reference: Mapped[str | None] = mapped_column(String(500))


class Device(Base, TenantOwnedMixin):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "identifier", name="uq_device_identifier_tenant"),
        Index("ix_device_rack_u", "tenant_id", "rack_id", "start_u", "face"),
    )
    rack_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("racks.id", ondelete="SET NULL"), index=True
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("device_templates.id")
    )
    identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    device_type: Mapped[str] = mapped_column(String(80), nullable=False)
    rack_units: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    start_u: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    face: Mapped[str] = mapped_column(String(20), default="front", nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String(180), index=True)
    asset_tag: Mapped[str | None] = mapped_column(String(180), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    instance_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class Port(Base, TenantOwnedMixin):
    __tablename__ = "ports"
    __table_args__ = (
        UniqueConstraint("tenant_id", "device_id", "identifier", name="uq_port_device_identifier"),
        Index("ix_port_tenant_device_position", "tenant_id", "device_id", "position_index"),
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(180), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(80), nullable=False)
    media_type: Mapped[str] = mapped_column(String(80), nullable=False)
    direction: Mapped[str] = mapped_column(String(30), default="bidirectional", nullable=False)
    front_or_rear: Mapped[str] = mapped_column(String(20), default="front", nullable=False)
    position_index: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="available", nullable=False)
    termination_type: Mapped[str | None] = mapped_column(String(80))


class PortMapping(Base, TenantOwnedMixin):
    __tablename__ = "port_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_port_id", "target_port_id", name="uq_port_mapping"),
        CheckConstraint("source_port_id <> target_port_id", name="ck_port_mapping_not_self"),
    )
    source_port_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_port_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mapping_type: Mapped[str] = mapped_column(String(50), default="front_rear", nullable=False)
    lane: Mapped[int | None] = mapped_column(Integer)


class Pathway(Base, TenantOwnedMixin):
    __tablename__ = "pathways"
    __table_args__ = (
        UniqueConstraint("tenant_id", "identifier", name="uq_pathway_identifier_tenant"),
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    pathway_type: Mapped[str] = mapped_column(String(80), nullable=False)
    capacity_area_mm2: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    cable_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {"allows_cables": True, "allowed_media": ["copper", "fiber"]},
        nullable=False,
    )


class PathwaySegment(Base, TenantOwnedMixin):
    __tablename__ = "pathway_segments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "pathway_id", "sequence", name="uq_pathway_segment_sequence"),
    )
    pathway_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("pathways.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    length_m: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    capacity_area_mm2: Mapped[float | None] = mapped_column(Float)
    reserved_percent: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    coordinates: Mapped[list[dict[str, float]]] = mapped_column(JSON, default=list, nullable=False)


class Cable(Base, TenantOwnedMixin):
    __tablename__ = "cables"
    __table_args__ = (
        UniqueConstraint("tenant_id", "identifier", name="uq_cable_identifier_tenant"),
        Index("ix_cable_tenant_status", "tenant_id", "installation_status"),
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    media_type: Mapped[str] = mapped_column(String(80), nullable=False)
    construction: Mapped[str] = mapped_column(String(80), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(120))
    part_number: Mapped[str | None] = mapped_column(String(120))
    color: Mapped[str | None] = mapped_column(String(50))
    length_m: Mapped[float | None] = mapped_column(Float)
    measured_length_m: Mapped[float | None] = mapped_column(Float)
    strand_count: Mapped[int | None] = mapped_column(Integer)
    pair_count: Mapped[int | None] = mapped_column(Integer)
    installation_status: Mapped[CableStatus] = mapped_column(
        SAEnum(CableStatus, native_enum=False), default=CableStatus.PLANNED, nullable=False
    )
    installer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    test_status: Mapped[str | None] = mapped_column(String(30))
    owner_organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    maintenance_owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    custom_attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class CableTermination(Base, TenantOwnedMixin):
    __tablename__ = "cable_terminations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "cable_id", "side", name="uq_cable_side"),
        Index(
            "uq_active_cable_port_termination",
            "tenant_id",
            "port_id",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("side IN ('A', 'B')", name="ck_cable_termination_side"),
    )
    cable_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    side: Mapped[str] = mapped_column(String(1), nullable=False)
    port_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ports.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    strand: Mapped[int | None] = mapped_column(Integer)
    pair: Mapped[int | None] = mapped_column(Integer)


class CableRouteSegment(Base, TenantOwnedMixin):
    __tablename__ = "cable_route_segments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "cable_id", "sequence", name="uq_cable_route_sequence"),
        CheckConstraint(
            "(start_offset_m IS NULL AND end_offset_m IS NULL AND geometry_hash IS NULL) OR "
            "(start_offset_m IS NOT NULL AND end_offset_m IS NOT NULL AND geometry_hash IS NOT NULL "
            "AND start_offset_m >= 0 AND end_offset_m >= 0)",
            name="ck_cable_route_portion",
        ),
    )
    cable_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pathway_segment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("pathway_segments.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    start_offset_m: Mapped[float | None] = mapped_column(Float)
    end_offset_m: Mapped[float | None] = mapped_column(Float)
    geometry_hash: Mapped[str | None] = mapped_column(String(64))


class WorkOrder(Base, TenantOwnedMixin):
    __tablename__ = "work_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "work_order_number", name="uq_work_order_number_tenant"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id"), index=True
    )
    cable_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cables.id"), index=True
    )
    work_order_number: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    assigned_organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user_identities.id")
    )
    status: Mapped[WorkOrderStatus] = mapped_column(
        SAEnum(WorkOrderStatus, native_enum=False), default=WorkOrderStatus.DRAFT, nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class TestRecord(Base, TenantOwnedMixin):
    __tablename__ = "test_records"
    cable_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("work_orders.id", ondelete="SET NULL")
    )
    tester_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    measurements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    tested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attachment_name: Mapped[str | None] = mapped_column(String(500))
    attachment_object_key: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="submitted", nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Label(Base, TenantOwnedMixin):
    __tablename__ = "labels"
    standard_profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("standard_profiles.id"), nullable=False
    )
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    template_name: Mapped[str] = mapped_column(String(120), nullable=False)
    qr_payload: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class AuditEvent(Base, UUIDMixin):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_tenant_time", "tenant_id", "timestamp"),
        Index("ix_audit_object", "tenant_id", "object_type", "object_id"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_organization_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    object_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(100), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)

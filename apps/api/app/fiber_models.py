"""Tenant-owned physical connectivity extensions for Fiber and copper channels.

Revision 004 owns the original bundle/strand/cassette/splice tables. Revision 005
adds normalized active endpoint claims, port terminations, copper pairs, logical
channels, passive breakouts and OTDR records. Claims are the cross-resource
occupancy boundary: a strand side or physical port can have only one active owner.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, Cable, CableTermination, Device, Port, Project, TenantOwnedMixin


# Referenced composite keys must exist in both ORM metadata and frozen migrations.
for _parent in (Cable, Device, Project, Port, CableTermination):
    _name = f"uq_{_parent.__tablename__}_fiber_tenant_id"
    if _name not in {_index.name for _index in _parent.__table__.indexes}:
        Index(_name, _parent.__table__.c.tenant_id, _parent.__table__.c.id, unique=True)


def tenant_key(name: str):
    return UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id")


def tenant_fk(column: str, parent: str, *, ondelete: str = "RESTRICT"):
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{parent}.tenant_id", f"{parent}.id"],
        ondelete=ondelete,
    )


class FiberBundle(Base, TenantOwnedMixin):
    __tablename__ = "fiber_bundles"
    __table_args__ = (
        tenant_key("fiber_bundles"),
        tenant_fk("cable_id", "cables"),
        UniqueConstraint("tenant_id", "cable_id", name="uq_fiber_bundle_cable"),
        CheckConstraint("strand_count BETWEEN 1 AND 576", name="ck_fiber_bundle_count"),
    )
    cable_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    strand_count: Mapped[int] = mapped_column(Integer, nullable=False)


class FiberStrand(Base, TenantOwnedMixin):
    __tablename__ = "fiber_strands"
    __table_args__ = (
        tenant_key("fiber_strands"),
        tenant_fk("bundle_id", "fiber_bundles"),
        UniqueConstraint("tenant_id", "bundle_id", "number", name="uq_fiber_strand_number"),
        CheckConstraint("number BETWEEN 1 AND 576", name="ck_fiber_strand_number"),
    )
    bundle_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)


class FiberCassette(Base, TenantOwnedMixin):
    __tablename__ = "fiber_cassettes"
    __table_args__ = (
        tenant_key("fiber_cassettes"),
        tenant_fk("device_id", "devices"),
        tenant_fk("project_id", "projects"),
        UniqueConstraint("tenant_id", "device_id", "name", name="uq_fiber_cassette_name"),
        CheckConstraint("slot_count BETWEEN 1 AND 288", name="ck_fiber_cassette_count"),
    )
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    slot_count: Mapped[int] = mapped_column(Integer, nullable=False)


class FiberCassetteSlot(Base, TenantOwnedMixin):
    __tablename__ = "fiber_cassette_slots"
    __table_args__ = (
        tenant_key("fiber_cassette_slots"),
        tenant_fk("cassette_id", "fiber_cassettes"),
        UniqueConstraint("tenant_id", "cassette_id", "number", name="uq_fiber_slot_number"),
        CheckConstraint("number BETWEEN 1 AND 288", name="ck_fiber_slot_number"),
    )
    cassette_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)


class FiberSplice(Base, TenantOwnedMixin):
    __tablename__ = "fiber_splices"
    __table_args__ = (
        tenant_key("fiber_splices"),
        tenant_fk("slot_id", "fiber_cassette_slots"),
        CheckConstraint("loss_db >= 0 AND loss_db <= 10", name="ck_fiber_splice_loss"),
        Index(
            "uq_fiber_active_splice_slot",
            "tenant_id",
            "slot_id",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
    slot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    loss_db: Mapped[float] = mapped_column(Float, nullable=False)


class FiberSpliceEnd(Base, TenantOwnedMixin):
    __tablename__ = "fiber_splice_ends"
    __table_args__ = (
        tenant_key("fiber_splice_ends"),
        tenant_fk("splice_id", "fiber_splices"),
        tenant_fk("strand_id", "fiber_strands"),
        UniqueConstraint("tenant_id", "strand_id", "side", name="uq_fiber_endpoint_claim"),
        UniqueConstraint("tenant_id", "splice_id", "end_number", name="uq_fiber_splice_end"),
        CheckConstraint("side IN ('A', 'B')", name="ck_fiber_endpoint_side"),
        CheckConstraint("end_number IN (1, 2)", name="ck_fiber_splice_end_number"),
    )
    splice_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    strand_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    side: Mapped[str] = mapped_column(String(1), nullable=False)
    end_number: Mapped[int] = mapped_column(Integer, nullable=False)


class FiberEndpointClaim(Base, TenantOwnedMixin):
    __tablename__ = "fiber_endpoint_claims"
    __table_args__ = (
        tenant_key("fiber_endpoint_claims"),
        tenant_fk("strand_id", "fiber_strands"),
        CheckConstraint("side IN ('A', 'B')", name="ck_fiber_claim_side"),
        CheckConstraint(
            "owner_type IN ('splice', 'fiber_termination', 'breakout_leg')",
            name="ck_fiber_claim_owner_type",
        ),
        Index(
            "uq_fiber_active_endpoint_claim",
            "tenant_id",
            "strand_id",
            "side",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_fiber_claim_owner", "tenant_id", "owner_type", "owner_id"),
    )
    strand_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    side: Mapped[str] = mapped_column(String(1), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class PhysicalPortClaim(Base, TenantOwnedMixin):
    __tablename__ = "physical_port_claims"
    __table_args__ = (
        tenant_key("physical_port_claims"),
        tenant_fk("port_id", "ports"),
        CheckConstraint(
            "owner_type IN ('cable_termination', 'fiber_termination')",
            name="ck_physical_claim_owner_type",
        ),
        Index(
            "uq_active_physical_port_claim",
            "tenant_id",
            "port_id",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_physical_claim_owner", "tenant_id", "owner_type", "owner_id"),
    )
    port_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class FiberPortTermination(Base, TenantOwnedMixin):
    __tablename__ = "fiber_port_terminations"
    __table_args__ = (
        tenant_key("fiber_port_terminations"),
        tenant_fk("project_id", "projects"),
        tenant_fk("strand_id", "fiber_strands"),
        tenant_fk("port_id", "ports"),
        CheckConstraint("side IN ('A', 'B')", name="ck_fiber_termination_side"),
        CheckConstraint(
            "connection_type IN ('connector', 'pigtail', 'fusion', 'mechanical')",
            name="ck_fiber_termination_type",
        ),
        CheckConstraint("loss_db >= 0 AND loss_db <= 10", name="ck_fiber_termination_loss"),
        Index(
            "uq_active_fiber_port_termination_endpoint",
            "tenant_id",
            "strand_id",
            "side",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    strand_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    side: Mapped[str] = mapped_column(String(1), nullable=False)
    port_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    connection_type: Mapped[str] = mapped_column(String(24), nullable=False)
    loss_db: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class CopperPair(Base, TenantOwnedMixin):
    __tablename__ = "copper_pairs"
    __table_args__ = (
        tenant_key("copper_pairs"),
        tenant_fk("cable_id", "cables"),
        UniqueConstraint("tenant_id", "cable_id", "number", name="uq_copper_pair_number"),
        CheckConstraint("number BETWEEN 1 AND 600", name="ck_copper_pair_number"),
    )
    cable_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    color_code: Mapped[str | None] = mapped_column(String(80))


class ConnectivityChannel(Base, TenantOwnedMixin):
    __tablename__ = "connectivity_channels"
    __table_args__ = (
        tenant_key("connectivity_channels"),
        tenant_fk("project_id", "projects"),
        UniqueConstraint("tenant_id", "project_id", "identifier", name="uq_channel_identifier"),
        CheckConstraint("medium IN ('fiber', 'copper')", name="ck_channel_medium"),
        CheckConstraint(
            "topology IN ('simplex', 'duplex', 'quad', 'bundle', 'ethernet')",
            name="ck_channel_topology",
        ),
        CheckConstraint(
            "status IN ('planned', 'active', 'reserved', 'retired')",
            name="ck_channel_status",
        ),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    medium: Mapped[str] = mapped_column(String(12), nullable=False)
    topology: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)


class ChannelMember(Base, TenantOwnedMixin):
    __tablename__ = "channel_members"
    __table_args__ = (
        tenant_key("channel_members"),
        tenant_fk("channel_id", "connectivity_channels"),
        tenant_fk("fiber_strand_id", "fiber_strands"),
        tenant_fk("copper_pair_id", "copper_pairs"),
        UniqueConstraint("tenant_id", "channel_id", "sequence", name="uq_channel_member_sequence"),
        CheckConstraint(
            "(fiber_strand_id IS NOT NULL AND copper_pair_id IS NULL) OR "
            "(fiber_strand_id IS NULL AND copper_pair_id IS NOT NULL)",
            name="ck_channel_member_exactly_one",
        ),
        CheckConstraint("sequence BETWEEN 1 AND 600", name="ck_channel_member_sequence"),
        Index(
            "uq_active_channel_fiber_member",
            "tenant_id",
            "fiber_strand_id",
            unique=True,
            sqlite_where=text("deleted_at IS NULL AND fiber_strand_id IS NOT NULL"),
            postgresql_where=text("deleted_at IS NULL AND fiber_strand_id IS NOT NULL"),
        ),
        Index(
            "uq_active_channel_copper_member",
            "tenant_id",
            "copper_pair_id",
            unique=True,
            sqlite_where=text("deleted_at IS NULL AND copper_pair_id IS NOT NULL"),
            postgresql_where=text("deleted_at IS NULL AND copper_pair_id IS NOT NULL"),
        ),
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    fiber_strand_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    copper_pair_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class FiberBreakout(Base, TenantOwnedMixin):
    __tablename__ = "fiber_breakouts"
    __table_args__ = (
        tenant_key("fiber_breakouts"),
        tenant_fk("project_id", "projects"),
        tenant_fk("device_id", "devices"),
        UniqueConstraint(
            "tenant_id", "project_id", "identifier",
            name="uq_fiber_breakout_identifier",
        ),
        CheckConstraint("mode IN ('fanout', 'fanin', 'passive')", name="ck_fiber_breakout_mode"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)


class FiberBreakoutLeg(Base, TenantOwnedMixin):
    __tablename__ = "fiber_breakout_legs"
    __table_args__ = (
        tenant_key("fiber_breakout_legs"),
        tenant_fk("breakout_id", "fiber_breakouts"),
        tenant_fk("parent_strand_id", "fiber_strands"),
        tenant_fk("child_strand_id", "fiber_strands"),
        UniqueConstraint("tenant_id", "breakout_id", "leg_number", name="uq_breakout_leg_number"),
        CheckConstraint("leg_number BETWEEN 1 AND 576", name="ck_breakout_leg_number"),
        CheckConstraint("parent_side IN ('A', 'B')", name="ck_breakout_parent_side"),
        CheckConstraint("child_side IN ('A', 'B')", name="ck_breakout_child_side"),
        CheckConstraint(
            "parent_strand_id <> child_strand_id OR parent_side <> child_side",
            name="ck_breakout_distinct_endpoints",
        ),
        CheckConstraint("loss_db >= 0 AND loss_db <= 10", name="ck_breakout_loss"),
    )
    breakout_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    leg_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String(180))
    parent_strand_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    parent_side: Mapped[str] = mapped_column(String(1), nullable=False)
    child_strand_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    child_side: Mapped[str] = mapped_column(String(1), nullable=False)
    loss_db: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class OtdrRecord(Base, TenantOwnedMixin):
    __tablename__ = "otdr_records"
    __table_args__ = (
        tenant_key("otdr_records"),
        tenant_fk("project_id", "projects"),
        tenant_fk("cable_id", "cables"),
        tenant_fk("strand_id", "fiber_strands"),
        CheckConstraint("direction IN ('A', 'B')", name="ck_otdr_direction"),
        CheckConstraint("wavelength_nm BETWEEN 600 AND 1700", name="ck_otdr_wavelength"),
        CheckConstraint("total_length_m IS NULL OR total_length_m >= 0", name="ck_otdr_length"),
        CheckConstraint(
            "end_to_end_loss_db IS NULL OR (end_to_end_loss_db >= 0 AND end_to_end_loss_db <= 100)",
            name="ck_otdr_total_loss",
        ),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    cable_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    strand_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    direction: Mapped[str] = mapped_column(String(1), nullable=False)
    wavelength_nm: Mapped[int] = mapped_column(Integer, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_object_key: Mapped[str | None] = mapped_column(String(500))
    total_length_m: Mapped[float | None] = mapped_column(Float)
    end_to_end_loss_db: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class OtdrEvent(Base, TenantOwnedMixin):
    __tablename__ = "otdr_events"
    __table_args__ = (
        tenant_key("otdr_events"),
        tenant_fk("record_id", "otdr_records"),
        UniqueConstraint("tenant_id", "record_id", "sequence", name="uq_otdr_event_sequence"),
        CheckConstraint("sequence BETWEEN 1 AND 10000", name="ck_otdr_event_sequence"),
        CheckConstraint("distance_m >= 0", name="ck_otdr_event_distance"),
        CheckConstraint(
            "event_type IN ('launch', 'connector', 'splice', 'bend', "
            "'reflective', 'end', 'unknown')",
            name="ck_otdr_event_type",
        ),
        CheckConstraint(
            "loss_db IS NULL OR (loss_db >= 0 AND loss_db <= 100)",
            name="ck_otdr_event_loss",
        ),
        CheckConstraint(
            "reflectance_db IS NULL OR (reflectance_db >= -120 AND reflectance_db <= 20)",
            name="ck_otdr_event_reflectance",
        ),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_otdr_event_confidence"),
        CheckConstraint(
            "linked_kind IS NULL OR linked_kind IN ('splice', 'fiber_termination', 'breakout_leg')",
            name="ck_otdr_event_link_kind",
        ),
        CheckConstraint(
            "(linked_kind IS NULL AND linked_id IS NULL) OR "
            "(linked_kind IS NOT NULL AND linked_id IS NOT NULL)",
            name="ck_otdr_event_link_pair",
        ),
    )
    record_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    loss_db: Mapped[float | None] = mapped_column(Float)
    reflectance_db: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    linked_kind: Mapped[str | None] = mapped_column(String(32))
    linked_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    link_offset_m: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(String(1000))


REVISION_004_TABLES = (
    "fiber_bundles",
    "fiber_strands",
    "fiber_cassettes",
    "fiber_cassette_slots",
    "fiber_splices",
    "fiber_splice_ends",
)

REVISION_005_TABLES = (
    "fiber_endpoint_claims",
    "physical_port_claims",
    "fiber_port_terminations",
    "copper_pairs",
    "connectivity_channels",
    "channel_members",
    "fiber_breakouts",
    "fiber_breakout_legs",
    "otdr_records",
    "otdr_events",
)

FIBER_TABLES = REVISION_004_TABLES + REVISION_005_TABLES

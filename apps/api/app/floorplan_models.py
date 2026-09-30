"""Versioned 2D floor-plan persistence.

Documents are immutable revisions. FloorPlan points to the current and published
revision numbers, avoiding a circular foreign key while preserving history.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, Location, Project, TenantOwnedMixin


def _ensure_parent_index(table, name: str) -> None:
    if not any(index.name == name for index in table.indexes):
        Index(name, table.c.tenant_id, table.c.id, unique=True)


# Project already receives this key in revision 004/fiber_models. The guard keeps
# metadata.create_all usable when floor-plan models are imported independently.
_ensure_parent_index(Project.__table__, "uq_projects_fiber_tenant_id")
_ensure_parent_index(Location.__table__, "uq_locations_floor_plan_tenant_id")


def tenant_key(name: str):
    return UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id")


def tenant_fk(column: str, parent: str, *, ondelete: str = "RESTRICT"):
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{parent}.tenant_id", f"{parent}.id"],
        ondelete=ondelete,
    )


class FloorPlan(Base, TenantOwnedMixin):
    __tablename__ = "floor_plans"
    __table_args__ = (
        tenant_key("floor_plans"),
        tenant_fk("project_id", "projects"),
        tenant_fk("location_id", "locations"),
        UniqueConstraint(
            "tenant_id", "location_id", "name", name="uq_floor_plan_location_name"
        ),
        CheckConstraint("units IN ('mm', 'm', 'ft')", name="ck_floor_plan_units"),
        CheckConstraint(
            "canvas_width > 0 AND canvas_width <= 1000000",
            name="ck_floor_plan_width",
        ),
        CheckConstraint(
            "canvas_height > 0 AND canvas_height <= 1000000",
            name="ck_floor_plan_height",
        ),
        CheckConstraint(
            "current_revision_number >= 1",
            name="ck_floor_plan_current_revision",
        ),
        CheckConstraint(
            "published_revision_number IS NULL OR "
            "(published_revision_number >= 1 AND "
            "published_revision_number <= current_revision_number)",
            name="ck_floor_plan_published_revision",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_floor_plan_status",
        ),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    units: Mapped[str] = mapped_column(String(8), nullable=False, default="mm")
    canvas_width: Mapped[float] = mapped_column(nullable=False)
    canvas_height: Mapped[float] = mapped_column(nullable=False)
    background_reference: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    current_revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_revision_number: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FloorPlanRevision(Base, TenantOwnedMixin):
    __tablename__ = "floor_plan_revisions"
    __table_args__ = (
        tenant_key("floor_plan_revisions"),
        tenant_fk("floor_plan_id", "floor_plans"),
        UniqueConstraint(
            "tenant_id",
            "floor_plan_id",
            "revision_number",
            name="uq_floor_plan_revision_number",
        ),
        CheckConstraint("revision_number >= 1", name="ck_floor_plan_revision_number"),
        CheckConstraint("schema_version = 1", name="ck_floor_plan_schema_version"),
    )
    floor_plan_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    change_summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    restored_from_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True)
    )


FLOOR_PLAN_TABLES = ("floor_plans", "floor_plan_revisions")

"""Tenant-owned, immutable-revision 2D floor-plan persistence.

The mutable FloorPlan row is only a small head pointer. Every editor save creates
an immutable FloorPlanRevision containing a complete, checksummed document. This
keeps conflict handling, publication, history and restoration deterministic.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, Location, Project, TenantOwnedMixin


def _ensure_parent_index(model, name: str) -> None:
    if name not in {index.name for index in model.__table__.indexes}:
        Index(name, model.__table__.c.tenant_id, model.__table__.c.id, unique=True)


_ensure_parent_index(Project, "uq_projects_floorplan_tenant_id")
_ensure_parent_index(Location, "uq_locations_floorplan_tenant_id")


def _tenant_key(table: str) -> UniqueConstraint:
    return UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id")


def _tenant_fk(column: str, table: str, *, ondelete: str = "RESTRICT") -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{table}.tenant_id", f"{table}.id"],
        ondelete=ondelete,
    )


class FloorPlan(Base, TenantOwnedMixin):
    __tablename__ = "floor_plans"
    __table_args__ = (
        _tenant_key("floor_plans"),
        _tenant_fk("project_id", "projects"),
        _tenant_fk("location_id", "locations"),
        UniqueConstraint(
            "tenant_id", "project_id", "location_id", "name",
            name="uq_floor_plan_scope_name",
        ),
        CheckConstraint("width_mm > 0 AND width_mm <= 1000000", name="ck_floor_plan_width"),
        CheckConstraint("height_mm > 0 AND height_mm <= 1000000", name="ck_floor_plan_height"),
        CheckConstraint("head_revision >= 1", name="ck_floor_plan_head_revision"),
        CheckConstraint(
            "published_revision IS NULL OR published_revision >= 1",
            name="ck_floor_plan_published_revision",
        ),
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_floor_plan_status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), default="mm", nullable=False)
    width_mm: Mapped[float] = mapped_column(Float, nullable=False)
    height_mm: Mapped[float] = mapped_column(Float, nullable=False)
    head_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    published_revision: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    background_object_key: Mapped[str | None] = mapped_column(String(500))


class FloorPlanRevision(Base, TenantOwnedMixin):
    __tablename__ = "floor_plan_revisions"
    __table_args__ = (
        _tenant_key("floor_plan_revisions"),
        _tenant_fk("floor_plan_id", "floor_plans"),
        UniqueConstraint(
            "tenant_id", "floor_plan_id", "revision",
            name="uq_floor_plan_revision_number",
        ),
        CheckConstraint("revision >= 1", name="ck_floor_plan_revision_number"),
        CheckConstraint("schema_version = 1", name="ck_floor_plan_schema_version"),
    )

    floor_plan_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    restored_from_revision: Mapped[int | None] = mapped_column(Integer)


FLOOR_PLAN_TABLES = ("floor_plans", "floor_plan_revisions")

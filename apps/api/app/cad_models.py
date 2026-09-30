"""Append-only CAD exchange history. Business objects remain the authority."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models import Base, TenantOwnedMixin


class CadSnapshot(Base, TenantOwnedMixin):
    __tablename__ = "cad_snapshots"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class CadImportRevision(Base, TenantOwnedMixin):
    __tablename__ = "cad_import_revisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "fingerprint"),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"], ["cad_snapshots.tenant_id", "cad_snapshots.id"]
        ),
    )
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    filename: Mapped[str] = mapped_column(String(240), nullable=False)
    source: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class CadApplication(Base, TenantOwnedMixin):
    __tablename__ = "cad_applications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "revision_id"),
        ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            ["cad_import_revisions.tenant_id", "cad_import_revisions.id"],
        ),
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


@event.listens_for(Session, "before_flush")
def immutable_cad_history(session: Session, _context, _instances):
    for row in session.dirty.union(session.deleted):
        if isinstance(row, (CadSnapshot, CadImportRevision, CadApplication)):
            raise PermissionError("CAD originals, revisions and applications are immutable")

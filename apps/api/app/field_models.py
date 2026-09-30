"""Tenant-scoped idempotency receipts for offline field mutations."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TenantOwnedMixin


class FieldMutationReceipt(Base, TenantOwnedMixin):
    __tablename__ = "field_mutation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key_hash",
            name="uq_field_mutation_receipt_key",
        ),
        Index("ix_field_mutation_receipt_expiry", "expires_at"),
    )

    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    actor_scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)
    response_headers: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


FIELD_TABLES = ("field_mutation_receipts",)

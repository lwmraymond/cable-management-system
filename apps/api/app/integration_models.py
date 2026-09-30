"""Tenant-owned NetBox synchronization and signed webhook delivery state."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TenantOwnedMixin


def tenant_key(table: str) -> UniqueConstraint:
    return UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id")


def tenant_fk(column: str, parent: str, *, ondelete: str = "RESTRICT") -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{parent}.tenant_id", f"{parent}.id"],
        ondelete=ondelete,
    )


class NetBoxSyncCursor(Base, TenantOwnedMixin):
    __tablename__ = "netbox_sync_cursors"
    __table_args__ = (
        tenant_key("netbox_sync_cursors"),
        UniqueConstraint("tenant_id", "name", name="uq_netbox_sync_cursor_name"),
        CheckConstraint("status IN ('idle', 'running', 'succeeded', 'failed')", name="ck_netbox_sync_status"),
    )

    name: Mapped[str] = mapped_column(String(180), nullable=False)
    resource: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    cursor_value: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20), default="idle", nullable=False)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    records_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ExternalObjectMap(Base, TenantOwnedMixin):
    __tablename__ = "external_object_maps"
    __table_args__ = (
        tenant_key("external_object_maps"),
        UniqueConstraint(
            "tenant_id", "adapter", "external_type", "external_id",
            name="uq_external_object_identity",
        ),
        Index("ix_external_object_local", "tenant_id", "local_type", "local_id"),
    )

    adapter: Mapped[str] = mapped_column(String(50), nullable=False)
    external_type: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    local_type: Mapped[str] = mapped_column(String(100), nullable=False)
    local_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    external_version: Mapped[str | None] = mapped_column(String(255))
    snapshot_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class WebhookEndpoint(Base, TenantOwnedMixin):
    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        tenant_key("webhook_endpoints"),
        UniqueConstraint("tenant_id", "name", name="uq_webhook_endpoint_name"),
    )

    name: Mapped[str] = mapped_column(String(180), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    secret_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8, nullable=False)


class WebhookOutbox(Base, TenantOwnedMixin):
    __tablename__ = "webhook_outbox"
    __table_args__ = (
        tenant_key("webhook_outbox"),
        tenant_fk("endpoint_id", "webhook_endpoints"),
        UniqueConstraint("tenant_id", "event_id", name="uq_webhook_outbox_event"),
        CheckConstraint(
            "state IN ('pending', 'sending', 'retry', 'delivered', 'dead')",
            name="ck_webhook_outbox_state",
        ),
        Index("ix_webhook_outbox_due", "tenant_id", "state", "next_attempt_at"),
    )

    endpoint_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(180), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))


class WebhookDeliveryAttempt(Base, TenantOwnedMixin):
    __tablename__ = "webhook_delivery_attempts"
    __table_args__ = (
        tenant_key("webhook_delivery_attempts"),
        tenant_fk("outbox_id", "webhook_outbox"),
        UniqueConstraint(
            "tenant_id", "outbox_id", "attempt_number",
            name="uq_webhook_delivery_attempt_number",
        ),
    )

    outbox_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    signature_timestamp: Mapped[int] = mapped_column(Integer, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    response_excerpt: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(1000))


class WebhookInboundReceipt(Base, TenantOwnedMixin):
    __tablename__ = "webhook_inbound_receipts"
    __table_args__ = (
        tenant_key("webhook_inbound_receipts"),
        UniqueConstraint(
            "tenant_id", "endpoint_name", "event_id",
            name="uq_webhook_inbound_event",
        ),
        Index("ix_webhook_inbound_expiry", "expires_at"),
    )

    endpoint_name: Mapped[str] = mapped_column(String(180), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


INTEGRATION_TABLES = (
    "netbox_sync_cursors",
    "external_object_maps",
    "webhook_endpoints",
    "webhook_outbox",
    "webhook_delivery_attempts",
    "webhook_inbound_receipts",
)

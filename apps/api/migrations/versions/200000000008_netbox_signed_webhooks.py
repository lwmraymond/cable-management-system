"""NetBox synchronization and signed webhook outbox.

Revision ID: 200000000008
Revises: 200000000007
"""
from alembic import op
import sqlalchemy as sa

revision = "200000000008"
down_revision = "200000000007"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "netbox_sync_cursors",
    "external_object_maps",
    "webhook_endpoints",
    "webhook_outbox",
    "webhook_delivery_attempts",
    "webhook_inbound_receipts",
]


def _tenant_columns():
    return [
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _tenant_fk():
    return sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "netbox_sync_cursors",
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("resource", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("cursor_value", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("records_seen", sa.Integer(), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "status IN ('idle', 'running', 'succeeded', 'failed')",
            name="ck_netbox_sync_status",
        ),
        _tenant_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netbox_sync_cursors_tenant_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_netbox_sync_cursor_name"),
    )
    op.create_index(op.f("ix_netbox_sync_cursors_tenant_id"), "netbox_sync_cursors", ["tenant_id"])

    op.create_table(
        "external_object_maps",
        sa.Column("adapter", sa.String(length=50), nullable=False),
        sa.Column("external_type", sa.String(length=100), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("local_type", sa.String(length=100), nullable=False),
        sa.Column("local_id", sa.Uuid(), nullable=False),
        sa.Column("external_version", sa.String(length=255), nullable=True),
        sa.Column("snapshot_checksum", sa.String(length=64), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        *_tenant_columns(),
        _tenant_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_external_object_maps_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "adapter", "external_type", "external_id",
            name="uq_external_object_identity",
        ),
    )
    op.create_index(op.f("ix_external_object_maps_tenant_id"), "external_object_maps", ["tenant_id"])
    op.create_index(
        "ix_external_object_local",
        "external_object_maps",
        ["tenant_id", "local_type", "local_id"],
    )

    op.create_table(
        "webhook_endpoints",
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("secret_reference", sa.String(length=500), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        *_tenant_columns(),
        _tenant_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_webhook_endpoints_tenant_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_webhook_endpoint_name"),
    )
    op.create_index(op.f("ix_webhook_endpoints_tenant_id"), "webhook_endpoints", ["tenant_id"])

    op.create_table(
        "webhook_outbox",
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=180), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        *_tenant_columns(),
        sa.CheckConstraint(
            "state IN ('pending', 'sending', 'retry', 'delivered', 'dead')",
            name="ck_webhook_outbox_state",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "endpoint_id"],
            ["webhook_endpoints.tenant_id", "webhook_endpoints.id"],
            ondelete="RESTRICT",
        ),
        _tenant_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_webhook_outbox_tenant_id"),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_webhook_outbox_event"),
    )
    op.create_index(op.f("ix_webhook_outbox_tenant_id"), "webhook_outbox", ["tenant_id"])
    op.create_index(
        "ix_webhook_outbox_due",
        "webhook_outbox",
        ["tenant_id", "state", "next_attempt_at"],
    )

    op.create_table(
        "webhook_delivery_attempts",
        sa.Column("outbox_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("signature_timestamp", sa.Integer(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("response_excerpt", sa.Text(), nullable=True),
        sa.Column("error", sa.String(length=1000), nullable=True),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "outbox_id"],
            ["webhook_outbox.tenant_id", "webhook_outbox.id"],
            ondelete="RESTRICT",
        ),
        _tenant_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_webhook_delivery_attempts_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "outbox_id", "attempt_number",
            name="uq_webhook_delivery_attempt_number",
        ),
    )
    op.create_index(
        op.f("ix_webhook_delivery_attempts_tenant_id"),
        "webhook_delivery_attempts",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_webhook_delivery_attempts_outbox_id"),
        "webhook_delivery_attempts",
        ["outbox_id"],
    )

    op.create_table(
        "webhook_inbound_receipts",
        sa.Column("endpoint_name", sa.String(length=180), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_columns(),
        _tenant_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_webhook_inbound_receipts_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "endpoint_name", "event_id",
            name="uq_webhook_inbound_event",
        ),
    )
    op.create_index(
        op.f("ix_webhook_inbound_receipts_tenant_id"),
        "webhook_inbound_receipts",
        ["tenant_id"],
    )
    op.create_index(
        "ix_webhook_inbound_expiry",
        "webhook_inbound_receipts",
        ["expires_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                "USING (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid) "
                "WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid)"
            )


def downgrade() -> None:
    op.drop_index("ix_webhook_inbound_expiry", table_name="webhook_inbound_receipts")
    op.drop_index(op.f("ix_webhook_inbound_receipts_tenant_id"), table_name="webhook_inbound_receipts")
    op.drop_table("webhook_inbound_receipts")
    op.drop_index(op.f("ix_webhook_delivery_attempts_outbox_id"), table_name="webhook_delivery_attempts")
    op.drop_index(op.f("ix_webhook_delivery_attempts_tenant_id"), table_name="webhook_delivery_attempts")
    op.drop_table("webhook_delivery_attempts")
    op.drop_index("ix_webhook_outbox_due", table_name="webhook_outbox")
    op.drop_index(op.f("ix_webhook_outbox_tenant_id"), table_name="webhook_outbox")
    op.drop_table("webhook_outbox")
    op.drop_index(op.f("ix_webhook_endpoints_tenant_id"), table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
    op.drop_index("ix_external_object_local", table_name="external_object_maps")
    op.drop_index(op.f("ix_external_object_maps_tenant_id"), table_name="external_object_maps")
    op.drop_table("external_object_maps")
    op.drop_index(op.f("ix_netbox_sync_cursors_tenant_id"), table_name="netbox_sync_cursors")
    op.drop_table("netbox_sync_cursors")

"""Offline-field mutation idempotency receipts.

Revision ID: 200000000007
Revises: 200000000006
"""
from alembic import op
import sqlalchemy as sa

revision = "200000000007"
down_revision = "200000000006"
branch_labels = None
depends_on = None

TENANT_TABLES = ["field_mutation_receipts"]


def upgrade() -> None:
    op.create_table(
        "field_mutation_receipts",
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("actor_scope_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_content_type", sa.String(length=200), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column("response_headers", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key_hash",
            name="uq_field_mutation_receipt_key",
        ),
    )
    op.create_index(
        op.f("ix_field_mutation_receipts_tenant_id"),
        "field_mutation_receipts",
        ["tenant_id"],
    )
    op.create_index(
        "ix_field_mutation_receipt_expiry",
        "field_mutation_receipts",
        ["expires_at"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "field_mutation_receipts" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "field_mutation_receipts" FORCE ROW LEVEL SECURITY')
        op.execute(
            'CREATE POLICY tenant_isolation ON "field_mutation_receipts" '
            "USING (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid) "
            "WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid)"
        )


def downgrade() -> None:
    op.drop_index("ix_field_mutation_receipt_expiry", table_name="field_mutation_receipts")
    op.drop_index(op.f("ix_field_mutation_receipts_tenant_id"), table_name="field_mutation_receipts")
    op.drop_table("field_mutation_receipts")

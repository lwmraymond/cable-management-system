"""Versioned 2D Floor Plan documents and immutable revision history.

Revision ID: 200000000006
Revises: 200000000005
"""
from alembic import op
import sqlalchemy as sa

revision = "200000000006"
down_revision = "200000000005"
branch_labels = None
depends_on = None

TENANT_TABLES = ["floor_plans", "floor_plan_revisions"]


def _tenant_policy(table: str) -> str:
    return (
        f'CREATE POLICY tenant_isolation ON "{table}" '
        "USING (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid) "
        "WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid)"
    )


def upgrade() -> None:
    op.create_index(
        "uq_locations_floor_plan_tenant_id",
        "locations",
        ["tenant_id", "id"],
        unique=True,
    )
    op.create_table(
        "floor_plans",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("units", sa.String(length=8), nullable=False),
        sa.Column("canvas_width", sa.Float(), nullable=False),
        sa.Column("canvas_height", sa.Float(), nullable=False),
        sa.Column("background_reference", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_revision_number", sa.Integer(), nullable=False),
        sa.Column("published_revision_number", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "canvas_height > 0 AND canvas_height <= 1000000",
            name="ck_floor_plan_height",
        ),
        sa.CheckConstraint(
            "canvas_width > 0 AND canvas_width <= 1000000",
            name="ck_floor_plan_width",
        ),
        sa.CheckConstraint(
            "current_revision_number >= 1",
            name="ck_floor_plan_current_revision",
        ),
        sa.CheckConstraint(
            "published_revision_number IS NULL OR "
            "(published_revision_number >= 1 AND "
            "published_revision_number <= current_revision_number)",
            name="ck_floor_plan_published_revision",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_floor_plan_status",
        ),
        sa.CheckConstraint(
            "units IN ('mm', 'm', 'ft')",
            name="ck_floor_plan_units",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "location_id"],
            ["locations.tenant_id", "locations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_floor_plans_tenant_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "location_id",
            "name",
            name="uq_floor_plan_location_name",
        ),
    )
    op.create_index(
        op.f("ix_floor_plans_tenant_id"),
        "floor_plans",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "floor_plan_revisions",
        sa.Column("floor_plan_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("change_summary", sa.String(length=500), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("restored_from_revision_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revision_number >= 1",
            name="ck_floor_plan_revision_number",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="ck_floor_plan_schema_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "floor_plan_id"],
            ["floor_plans.tenant_id", "floor_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_floor_plan_revisions_tenant_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "floor_plan_id",
            "revision_number",
            name="uq_floor_plan_revision_number",
        ),
    )
    op.create_index(
        op.f("ix_floor_plan_revisions_tenant_id"),
        "floor_plan_revisions",
        ["tenant_id"],
        unique=False,
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(_tenant_policy(table))
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_floor_plan_revision_mutation()
            RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'floor_plan_revisions is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            "CREATE TRIGGER floor_plan_revisions_append_only "
            "BEFORE UPDATE OR DELETE ON floor_plan_revisions "
            "FOR EACH ROW EXECUTE FUNCTION reject_floor_plan_revision_mutation()"
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER floor_plan_revisions_append_only_update
            BEFORE UPDATE ON floor_plan_revisions
            BEGIN
              SELECT RAISE(ABORT, 'floor_plan_revisions is append-only');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER floor_plan_revisions_append_only_delete
            BEFORE DELETE ON floor_plan_revisions
            BEGIN
              SELECT RAISE(ABORT, 'floor_plan_revisions is append-only');
            END
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS floor_plan_revisions_append_only "
            "ON floor_plan_revisions"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS reject_floor_plan_revision_mutation"
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            "DROP TRIGGER IF EXISTS floor_plan_revisions_append_only_update"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS floor_plan_revisions_append_only_delete"
        )

    op.drop_index(
        op.f("ix_floor_plan_revisions_tenant_id"),
        table_name="floor_plan_revisions",
    )
    op.drop_table("floor_plan_revisions")
    op.drop_index(
        op.f("ix_floor_plans_tenant_id"),
        table_name="floor_plans",
    )
    op.drop_table("floor_plans")
    op.drop_index(
        "uq_locations_floor_plan_tenant_id",
        table_name="locations",
    )

"""Immutable, tenant-scoped CAD snapshots and reviewed file revisions."""

from alembic import op
import sqlalchemy as sa

revision = "200000000013"
down_revision = "200000000012"
branch_labels = None
depends_on = None
TENANT_TABLES = ("cad_snapshots", "cad_import_revisions", "cad_applications")


def columns():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    ]


def upgrade():
    op.create_table(
        "cad_snapshots",
        *columns(),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid()),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("source", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", "id"),
    )
    op.create_table(
        "cad_import_revisions",
        *columns(),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid()),
        sa.Column("snapshot_id", sa.Uuid()),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("filename", sa.String(240), nullable=False),
        sa.Column("source", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("parsed", sa.JSON(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "fingerprint"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"], ["cad_snapshots.tenant_id", "cad_snapshots.id"]
        ),
    )
    op.create_table(
        "cad_applications",
        *columns(),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("tenant_id", "revision_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            ["cad_import_revisions.tenant_id", "cad_import_revisions.id"],
        ),
    )
    for table in TENANT_TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION cad_history_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'CAD history is immutable'; END; $$"
        )
        for table in TENANT_TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                "USING (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid) "
                "WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid)"
            )
            op.execute(
                f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION cad_history_immutable()'
            )


def downgrade():
    # FORCE RLS can hide other tenants' history even from the table owner.
    # Never infer that dropping all tenants' originals is safe from a scoped count.
    raise RuntimeError("CAD history migration is forward-only; restore a pre-upgrade backup")

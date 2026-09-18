"""Frozen revision-005 upgrade segment. Never import application models."""
from alembic import op
import sqlalchemy as sa


def apply(common) -> None:
    TENANT_TABLES = common.TENANT_TABLES
    _backfill_claims = common._backfill_claims
    _tenant_columns = common._tenant_columns
    _tenant_constraints = common._tenant_constraints
    _tenant_index = common._tenant_index
    op.create_index(
        "uq_ports_fiber_tenant_id",
        "ports",
        ["tenant_id", "id"],
        unique=True,
    )
    op.create_index(
        "uq_cable_terminations_fiber_tenant_id",
        "cable_terminations",
        ["tenant_id", "id"],
        unique=True,
    )
    op.create_table(
        "connectivity_channels",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("identifier", sa.String(length=180), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("medium", sa.String(length=12), nullable=False),
        sa.Column("topology", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint("medium IN ('fiber', 'copper')", name="ck_channel_medium"),
        sa.CheckConstraint(
            "topology IN ('simplex', 'duplex', 'quad', 'bundle', 'ethernet')",
            name="ck_channel_topology",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'active', 'reserved', 'retired')",
            name="ck_channel_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "identifier", name="uq_channel_identifier"
        ),
        *_tenant_constraints("connectivity_channels"),
    )
    _tenant_index("connectivity_channels")
    op.create_table(
        "copper_pairs",
        sa.Column("cable_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("color_code", sa.String(length=80), nullable=True),
        *_tenant_columns(),
        sa.CheckConstraint("number BETWEEN 1 AND 600", name="ck_copper_pair_number"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "cable_id"],
            ["cables.tenant_id", "cables.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "cable_id", "number", name="uq_copper_pair_number"
        ),
        *_tenant_constraints("copper_pairs"),
    )
    _tenant_index("copper_pairs")
    op.create_table(
        "fiber_breakouts",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("identifier", sa.String(length=180), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "mode IN ('fanout', 'fanin', 'passive')",
            name="ck_fiber_breakout_mode",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "device_id"],
            ["devices.tenant_id", "devices.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "identifier", name="uq_fiber_breakout_identifier"
        ),
        *_tenant_constraints("fiber_breakouts"),
    )
    _tenant_index("fiber_breakouts")
    op.create_table(
        "fiber_endpoint_claims",
        sa.Column("strand_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(length=1), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint("side IN ('A', 'B')", name="ck_fiber_claim_side"),
        sa.CheckConstraint(
            "owner_type IN ('splice', 'fiber_termination', 'breakout_leg')",
            name="ck_fiber_claim_owner_type",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "strand_id"],
            ["fiber_strands.tenant_id", "fiber_strands.id"],
            ondelete="RESTRICT",
        ),
        *_tenant_constraints("fiber_endpoint_claims"),
    )
    _tenant_index("fiber_endpoint_claims")
    op.create_index(
        "ix_fiber_claim_owner",
        "fiber_endpoint_claims",
        ["tenant_id", "owner_type", "owner_id"],
        unique=False,
    )
    op.create_index(
        "uq_fiber_active_endpoint_claim",
        "fiber_endpoint_claims",
        ["tenant_id", "strand_id", "side"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

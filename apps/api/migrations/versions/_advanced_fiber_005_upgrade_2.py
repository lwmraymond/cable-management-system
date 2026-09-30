"""Frozen revision-005 upgrade segment. Never import application models."""
from alembic import op
import sqlalchemy as sa


def apply(common) -> None:
    TENANT_TABLES = common.TENANT_TABLES
    _backfill_claims = common._backfill_claims
    _tenant_columns = common._tenant_columns
    _tenant_constraints = common._tenant_constraints
    _tenant_index = common._tenant_index
    op.create_table(
        "physical_port_claims",
        sa.Column("port_id", sa.Uuid(), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "owner_type IN ('cable_termination', 'fiber_termination')",
            name="ck_physical_claim_owner_type",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "port_id"],
            ["ports.tenant_id", "ports.id"],
            ondelete="RESTRICT",
        ),
        *_tenant_constraints("physical_port_claims"),
    )
    _tenant_index("physical_port_claims")
    op.create_index(
        "ix_physical_claim_owner",
        "physical_port_claims",
        ["tenant_id", "owner_type", "owner_id"],
        unique=False,
    )
    op.create_index(
        "uq_active_physical_port_claim",
        "physical_port_claims",
        ["tenant_id", "port_id"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "fiber_port_terminations",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("strand_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(length=1), nullable=False),
        sa.Column("port_id", sa.Uuid(), nullable=False),
        sa.Column("connection_type", sa.String(length=24), nullable=False),
        sa.Column("loss_db", sa.Float(), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint("side IN ('A', 'B')", name="ck_fiber_termination_side"),
        sa.CheckConstraint(
            "connection_type IN ('connector', 'pigtail', 'fusion', 'mechanical')",
            name="ck_fiber_termination_type",
        ),
        sa.CheckConstraint(
            "loss_db >= 0 AND loss_db <= 10", name="ck_fiber_termination_loss"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "strand_id"],
            ["fiber_strands.tenant_id", "fiber_strands.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "port_id"],
            ["ports.tenant_id", "ports.id"],
            ondelete="RESTRICT",
        ),
        *_tenant_constraints("fiber_port_terminations"),
    )
    _tenant_index("fiber_port_terminations")
    op.create_index(
        "uq_active_fiber_port_termination_endpoint",
        "fiber_port_terminations",
        ["tenant_id", "strand_id", "side"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "channel_members",
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("fiber_strand_id", sa.Uuid(), nullable=True),
        sa.Column("copper_pair_id", sa.Uuid(), nullable=True),
        *_tenant_columns(),
        sa.CheckConstraint(
            "(fiber_strand_id IS NOT NULL AND copper_pair_id IS NULL) OR "
            "(fiber_strand_id IS NULL AND copper_pair_id IS NOT NULL)",
            name="ck_channel_member_exactly_one",
        ),
        sa.CheckConstraint(
            "sequence BETWEEN 1 AND 600", name="ck_channel_member_sequence"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["connectivity_channels.tenant_id", "connectivity_channels.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "fiber_strand_id"],
            ["fiber_strands.tenant_id", "fiber_strands.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "copper_pair_id"],
            ["copper_pairs.tenant_id", "copper_pairs.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "channel_id", "sequence", name="uq_channel_member_sequence"
        ),
        *_tenant_constraints("channel_members"),
    )
    _tenant_index("channel_members")
    op.create_index(
        "uq_active_channel_fiber_member",
        "channel_members",
        ["tenant_id", "fiber_strand_id"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL AND fiber_strand_id IS NOT NULL"),
        postgresql_where=sa.text("deleted_at IS NULL AND fiber_strand_id IS NOT NULL"),
    )
    op.create_index(
        "uq_active_channel_copper_member",
        "channel_members",
        ["tenant_id", "copper_pair_id"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL AND copper_pair_id IS NOT NULL"),
        postgresql_where=sa.text("deleted_at IS NULL AND copper_pair_id IS NOT NULL"),
    )
    op.create_table(
        "fiber_breakout_legs",
        sa.Column("breakout_id", sa.Uuid(), nullable=False),
        sa.Column("leg_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=180), nullable=True),
        sa.Column("parent_strand_id", sa.Uuid(), nullable=False),
        sa.Column("parent_side", sa.String(length=1), nullable=False),
        sa.Column("child_strand_id", sa.Uuid(), nullable=False),
        sa.Column("child_side", sa.String(length=1), nullable=False),
        sa.Column("loss_db", sa.Float(), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "leg_number BETWEEN 1 AND 576", name="ck_breakout_leg_number"
        ),
        sa.CheckConstraint(
            "parent_side IN ('A', 'B')", name="ck_breakout_parent_side"
        ),
        sa.CheckConstraint(
            "child_side IN ('A', 'B')", name="ck_breakout_child_side"
        ),
        sa.CheckConstraint(
            "parent_strand_id <> child_strand_id OR parent_side <> child_side",
            name="ck_breakout_distinct_endpoints",
        ),
        sa.CheckConstraint("loss_db >= 0 AND loss_db <= 10", name="ck_breakout_loss"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "breakout_id"],
            ["fiber_breakouts.tenant_id", "fiber_breakouts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_strand_id"],
            ["fiber_strands.tenant_id", "fiber_strands.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "child_strand_id"],
            ["fiber_strands.tenant_id", "fiber_strands.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "breakout_id", "leg_number", name="uq_breakout_leg_number"
        ),
        *_tenant_constraints("fiber_breakout_legs"),
    )
    _tenant_index("fiber_breakout_legs")

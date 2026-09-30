"""Advanced Fiber topology, normalized occupancy, channels, breakouts and OTDR.

Revision ID: 200000000005
Revises: 200000000004
Frozen migration delegates to immutable versioned helpers only.
"""
from alembic import op
import sqlalchemy as sa

revision = "200000000005"
down_revision = "200000000004"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "fiber_endpoint_claims",
    "physical_port_claims",
    "fiber_port_terminations",
    "copper_pairs",
    "connectivity_channels",
    "channel_members",
    "fiber_breakouts",
    "fiber_breakout_legs",
    "otdr_records",
    "otdr_events",
]

import importlib.util
from pathlib import Path


def _load(name: str):
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load frozen migration helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    common = _load("_advanced_fiber_005_common")
    _load("_advanced_fiber_005_upgrade_1").apply(common)
    _load("_advanced_fiber_005_upgrade_2").apply(common)
    _load("_advanced_fiber_005_upgrade_3").apply(common)


def downgrade() -> None:
    op.drop_index(op.f("ix_otdr_events_tenant_id"), table_name="otdr_events")
    op.drop_table("otdr_events")
    op.drop_index(op.f("ix_otdr_records_tenant_id"), table_name="otdr_records")
    op.drop_table("otdr_records")
    op.drop_index(op.f("ix_fiber_breakout_legs_tenant_id"), table_name="fiber_breakout_legs")
    op.drop_table("fiber_breakout_legs")
    op.drop_index("uq_active_channel_copper_member", table_name="channel_members",
                  sqlite_where=sa.text("deleted_at IS NULL AND copper_pair_id IS NOT NULL"),
                  postgresql_where=sa.text("deleted_at IS NULL AND copper_pair_id IS NOT NULL"))
    op.drop_index("uq_active_channel_fiber_member", table_name="channel_members",
                  sqlite_where=sa.text("deleted_at IS NULL AND fiber_strand_id IS NOT NULL"),
                  postgresql_where=sa.text("deleted_at IS NULL AND fiber_strand_id IS NOT NULL"))
    op.drop_index(op.f("ix_channel_members_tenant_id"), table_name="channel_members")
    op.drop_table("channel_members")
    op.drop_index(op.f("ix_connectivity_channels_tenant_id"), table_name="connectivity_channels")
    op.drop_table("connectivity_channels")
    op.drop_index(op.f("ix_copper_pairs_tenant_id"), table_name="copper_pairs")
    op.drop_table("copper_pairs")
    op.drop_index("uq_active_fiber_port_termination_endpoint",
                  table_name="fiber_port_terminations",
                  sqlite_where=sa.text("deleted_at IS NULL"),
                  postgresql_where=sa.text("deleted_at IS NULL"))
    op.drop_index(op.f("ix_fiber_port_terminations_tenant_id"),
                  table_name="fiber_port_terminations")
    op.drop_table("fiber_port_terminations")
    op.drop_index("uq_active_physical_port_claim", table_name="physical_port_claims",
                  sqlite_where=sa.text("deleted_at IS NULL"),
                  postgresql_where=sa.text("deleted_at IS NULL"))
    op.drop_index("ix_physical_claim_owner", table_name="physical_port_claims")
    op.drop_index(op.f("ix_physical_port_claims_tenant_id"),
                  table_name="physical_port_claims")
    op.drop_table("physical_port_claims")
    op.drop_index("uq_fiber_active_endpoint_claim", table_name="fiber_endpoint_claims",
                  sqlite_where=sa.text("deleted_at IS NULL"),
                  postgresql_where=sa.text("deleted_at IS NULL"))
    op.drop_index("ix_fiber_claim_owner", table_name="fiber_endpoint_claims")
    op.drop_index(op.f("ix_fiber_endpoint_claims_tenant_id"),
                  table_name="fiber_endpoint_claims")
    op.drop_table("fiber_endpoint_claims")
    op.drop_index(op.f("ix_fiber_breakouts_tenant_id"), table_name="fiber_breakouts")
    op.drop_table("fiber_breakouts")
    op.drop_index("uq_cable_terminations_fiber_tenant_id", table_name="cable_terminations")
    op.drop_index("uq_ports_fiber_tenant_id", table_name="ports")

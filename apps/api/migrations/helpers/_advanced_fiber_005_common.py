"""Frozen helpers for revision 200000000005. Never import application models."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

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

def _tenant_columns() -> list[sa.Column]:
    return [
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _tenant_constraints(table: str) -> list[sa.Constraint]:
    return [
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id"),
    ]


def _tenant_index(table: str) -> None:
    op.create_index(op.f(f"ix_{table}_tenant_id"), table, ["tenant_id"], unique=False)


def _backfill_claims() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    now = datetime.now(UTC)
    cable_terminations = sa.table(
        "cable_terminations",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("port_id", sa.Uuid()),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
    )
    physical_claims = sa.table(
        "physical_port_claims",
        sa.column("port_id", sa.Uuid()),
        sa.column("owner_type", sa.String()),
        sa.column("owner_id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("version", sa.Integer()),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
        sa.column("id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    termination_rows = bind.execute(
        sa.select(
            cable_terminations.c.id,
            cable_terminations.c.tenant_id,
            cable_terminations.c.port_id,
        ).where(cable_terminations.c.deleted_at.is_(None))
    ).mappings().all()
    if termination_rows:
        bind.execute(
            physical_claims.insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": row["tenant_id"],
                    "port_id": row["port_id"],
                    "owner_type": "cable_termination",
                    "owner_id": row["id"],
                    "version": 1,
                    "deleted_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
                for row in termination_rows
            ],
        )

    splice_ends = sa.table(
        "fiber_splice_ends",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("splice_id", sa.Uuid()),
        sa.column("strand_id", sa.Uuid()),
        sa.column("side", sa.String()),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
    )
    splices = sa.table(
        "fiber_splices",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
    )
    endpoint_claims = sa.table(
        "fiber_endpoint_claims",
        sa.column("strand_id", sa.Uuid()),
        sa.column("side", sa.String()),
        sa.column("owner_type", sa.String()),
        sa.column("owner_id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("version", sa.Integer()),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
        sa.column("id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    endpoint_rows = bind.execute(
        sa.select(
            splice_ends.c.tenant_id,
            splice_ends.c.splice_id,
            splice_ends.c.strand_id,
            splice_ends.c.side,
        )
        .select_from(
            splice_ends.join(
                splices,
                (splices.c.id == splice_ends.c.splice_id)
                & (splices.c.tenant_id == splice_ends.c.tenant_id),
            )
        )
        .where(
            splice_ends.c.deleted_at.is_(None),
            splices.c.deleted_at.is_(None),
        )
    ).mappings().all()
    if endpoint_rows:
        bind.execute(
            endpoint_claims.insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": row["tenant_id"],
                    "strand_id": row["strand_id"],
                    "side": row["side"],
                    "owner_type": "splice",
                    "owner_id": row["splice_id"],
                    "version": 1,
                    "deleted_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
                for row in endpoint_rows
            ],
        )

"""Retain terminated cable history while allowing released ports to be reused.

Revision ID: 200000000011
Revises: 200000000010
"""

from alembic import op
import sqlalchemy as sa

revision = "200000000011"
down_revision = "200000000010"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cable_terminations") as batch:
        batch.drop_constraint("uq_physical_port_termination", type_="unique")
        batch.create_index(
            "uq_active_cable_port_termination",
            ["tenant_id", "port_id"],
            unique=True,
            sqlite_where=sa.text("deleted_at IS NULL"),
            postgresql_where=sa.text("deleted_at IS NULL"),
        )


def downgrade():
    # Old versions cannot represent multiple historical occupants of a port.
    # Refuse before changing schema instead of deleting or rewriting history.
    duplicates = (
        "SELECT 1 FROM cable_terminations GROUP BY tenant_id, port_id HAVING COUNT(*) > 1 LIMIT 1"
    )
    if op.get_context().as_sql:
        op.execute(
            "DO $$ BEGIN IF EXISTS (" + duplicates + ") THEN "
            "RAISE EXCEPTION 'Cannot downgrade: historical cable port reuse exists'; "
            "END IF; END $$"
        )
    elif op.get_bind().execute(sa.text(duplicates)).first():
        raise RuntimeError(
            "Cannot downgrade: historical cable port reuse exists. "
            "Use the pre-upgrade backup with the previous application version."
        )
    with op.batch_alter_table("cable_terminations") as batch:
        batch.drop_index("uq_active_cable_port_termination")
        batch.create_unique_constraint("uq_physical_port_termination", ["tenant_id", "port_id"])

"""Persist partial tray traversal without rewriting existing full-segment routes.

Revision ID: 200000000012
Revises: 200000000011
"""

from alembic import op
import sqlalchemy as sa

revision = "200000000012"
down_revision = "200000000011"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cable_route_segments") as batch:
        batch.add_column(sa.Column("start_offset_m", sa.Float(), nullable=True))
        batch.add_column(sa.Column("end_offset_m", sa.Float(), nullable=True))
        batch.add_column(sa.Column("geometry_hash", sa.String(64), nullable=True))
        batch.create_check_constraint(
            "ck_cable_route_portion",
            "(start_offset_m IS NULL AND end_offset_m IS NULL AND geometry_hash IS NULL) OR (start_offset_m IS NOT NULL AND end_offset_m IS NOT NULL AND geometry_hash IS NOT NULL AND start_offset_m >= 0 AND end_offset_m >= 0)",
        )


def downgrade():
    exists = "SELECT 1 FROM cable_route_segments WHERE start_offset_m IS NOT NULL LIMIT 1"
    if op.get_context().as_sql:
        op.execute(
            "DO $$ BEGIN IF EXISTS ("
            + exists
            + ") THEN RAISE EXCEPTION 'Cannot downgrade: partial cable routes exist'; END IF; END $$"
        )
    elif op.get_bind().execute(sa.text(exists)).first():
        raise RuntimeError(
            "Cannot downgrade: partial cable routes exist. Restore the pre-upgrade backup with the previous application version."
        )
    with op.batch_alter_table("cable_route_segments") as batch:
        batch.drop_constraint("ck_cable_route_portion", type_="check")
        batch.drop_column("geometry_hash")
        batch.drop_column("end_offset_m")
        batch.drop_column("start_offset_m")

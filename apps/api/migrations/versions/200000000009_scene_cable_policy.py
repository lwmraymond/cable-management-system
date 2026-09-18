"""Persist per-pathway cable admission policy.

Revision ID: 200000000009
Revises: 200000000008
"""

from alembic import op
import sqlalchemy as sa

revision = "200000000009"
down_revision = "200000000008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "pathways",
        sa.Column(
            "cable_policy",
            sa.JSON(),
            nullable=False,
            server_default='{"allows_cables":true,"allowed_media":["copper","fiber"]}',
        ),
    )


def downgrade():
    with op.batch_alter_table("pathways") as batch:
        batch.drop_column("cable_policy")

"""Add account-owned personal and shared workspaces to existing tenant isolation.

Revision ID: 200000000010
Revises: 200000000009
"""

from alembic import op
import sqlalchemy as sa

revision = "200000000010"
down_revision = "200000000009"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(
            sa.Column("workspace_kind", sa.String(20), nullable=False, server_default="shared")
        )
        batch.add_column(sa.Column("workspace_owner_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_workspace_owner",
            "user_identities",
            ["workspace_owner_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_workspace_kind", "workspace_kind IN ('personal', 'shared')"
        )
        batch.create_check_constraint(
            "ck_personal_workspace_owner",
            "workspace_kind != 'personal' OR workspace_owner_id IS NOT NULL",
        )


def downgrade():
    with op.batch_alter_table("tenants") as batch:
        batch.drop_constraint("ck_personal_workspace_owner", type_="check")
        batch.drop_constraint("ck_workspace_kind", type_="check")
        batch.drop_constraint("fk_workspace_owner", type_="foreignkey")
        batch.drop_column("workspace_owner_id")
        batch.drop_column("workspace_kind")

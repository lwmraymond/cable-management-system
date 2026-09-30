"""Fiber bundles, strands and versioned cassette-slot splice topology.

Revision ID: 200000000004
Revises: 200000000003
Schema is frozen here; do not import current application model metadata.
"""
from alembic import op
import sqlalchemy as sa

revision = "200000000004"
down_revision = "200000000003"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "fiber_bundles", "fiber_strands", "fiber_cassettes", "fiber_cassette_slots",
    "fiber_splices", "fiber_splice_ends",
]

def upgrade() -> None:
    op.create_index('uq_cables_fiber_tenant_id', 'cables', ['tenant_id', 'id'], unique=True)
    op.create_index('uq_devices_fiber_tenant_id', 'devices', ['tenant_id', 'id'], unique=True)
    op.create_index('uq_projects_fiber_tenant_id', 'projects', ['tenant_id', 'id'], unique=True)
    op.create_table('fiber_bundles',
    sa.Column('cable_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=180), nullable=False),
    sa.Column('strand_count', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('strand_count BETWEEN 1 AND 576', name='ck_fiber_bundle_count'),
    sa.ForeignKeyConstraint(['tenant_id', 'cable_id'], ['cables.tenant_id', 'cables.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'cable_id', name='uq_fiber_bundle_cable'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_fiber_bundles_tenant_id')
    )
    op.create_index(op.f('ix_fiber_bundles_tenant_id'), 'fiber_bundles', ['tenant_id'], unique=False)
    op.create_table('fiber_cassettes',
    sa.Column('device_id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=180), nullable=False),
    sa.Column('slot_count', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('slot_count BETWEEN 1 AND 288', name='ck_fiber_cassette_count'),
    sa.ForeignKeyConstraint(['tenant_id', 'device_id'], ['devices.tenant_id', 'devices.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'device_id', 'name', name='uq_fiber_cassette_name'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_fiber_cassettes_tenant_id')
    )
    op.create_index(op.f('ix_fiber_cassettes_tenant_id'), 'fiber_cassettes', ['tenant_id'], unique=False)
    op.create_table('fiber_strands',
    sa.Column('bundle_id', sa.Uuid(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('number BETWEEN 1 AND 576', name='ck_fiber_strand_number'),
    sa.ForeignKeyConstraint(['tenant_id', 'bundle_id'], ['fiber_bundles.tenant_id', 'fiber_bundles.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'bundle_id', 'number', name='uq_fiber_strand_number'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_fiber_strands_tenant_id')
    )
    op.create_index(op.f('ix_fiber_strands_tenant_id'), 'fiber_strands', ['tenant_id'], unique=False)
    op.create_table('fiber_cassette_slots',
    sa.Column('cassette_id', sa.Uuid(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('number BETWEEN 1 AND 288', name='ck_fiber_slot_number'),
    sa.ForeignKeyConstraint(['tenant_id', 'cassette_id'], ['fiber_cassettes.tenant_id', 'fiber_cassettes.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'cassette_id', 'number', name='uq_fiber_slot_number'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_fiber_cassette_slots_tenant_id')
    )
    op.create_index(op.f('ix_fiber_cassette_slots_tenant_id'), 'fiber_cassette_slots', ['tenant_id'], unique=False)
    op.create_table('fiber_splices',
    sa.Column('slot_id', sa.Uuid(), nullable=False),
    sa.Column('loss_db', sa.Float(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('loss_db >= 0 AND loss_db <= 10', name='ck_fiber_splice_loss'),
    sa.ForeignKeyConstraint(['tenant_id', 'slot_id'], ['fiber_cassette_slots.tenant_id', 'fiber_cassette_slots.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_fiber_splices_tenant_id')
    )
    op.create_index(op.f('ix_fiber_splices_tenant_id'), 'fiber_splices', ['tenant_id'], unique=False)
    op.create_index('uq_fiber_active_splice_slot', 'fiber_splices', ['tenant_id', 'slot_id'], unique=True, sqlite_where=sa.text('deleted_at IS NULL'), postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_table('fiber_splice_ends',
    sa.Column('splice_id', sa.Uuid(), nullable=False),
    sa.Column('strand_id', sa.Uuid(), nullable=False),
    sa.Column('side', sa.String(length=1), nullable=False),
    sa.Column('end_number', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("side IN ('A', 'B')", name='ck_fiber_endpoint_side'),
    sa.CheckConstraint('end_number IN (1, 2)', name='ck_fiber_splice_end_number'),
    sa.ForeignKeyConstraint(['tenant_id', 'splice_id'], ['fiber_splices.tenant_id', 'fiber_splices.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'strand_id'], ['fiber_strands.tenant_id', 'fiber_strands.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_fiber_splice_ends_tenant_id'),
    sa.UniqueConstraint('tenant_id', 'splice_id', 'end_number', name='uq_fiber_splice_end'),
    sa.UniqueConstraint('tenant_id', 'strand_id', 'side', name='uq_fiber_endpoint_claim')
    )
    op.create_index(op.f('ix_fiber_splice_ends_tenant_id'), 'fiber_splice_ends', ['tenant_id'], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                "USING (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid) "
                "WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant', true), '')::uuid)"
            )


def downgrade() -> None:
    op.drop_index(op.f('ix_fiber_splice_ends_tenant_id'), table_name='fiber_splice_ends')
    op.drop_table('fiber_splice_ends')
    op.drop_index('uq_fiber_active_splice_slot', table_name='fiber_splices', sqlite_where=sa.text('deleted_at IS NULL'), postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_fiber_splices_tenant_id'), table_name='fiber_splices')
    op.drop_table('fiber_splices')
    op.drop_index(op.f('ix_fiber_cassette_slots_tenant_id'), table_name='fiber_cassette_slots')
    op.drop_table('fiber_cassette_slots')
    op.drop_index(op.f('ix_fiber_strands_tenant_id'), table_name='fiber_strands')
    op.drop_table('fiber_strands')
    op.drop_index(op.f('ix_fiber_cassettes_tenant_id'), table_name='fiber_cassettes')
    op.drop_table('fiber_cassettes')
    op.drop_index(op.f('ix_fiber_bundles_tenant_id'), table_name='fiber_bundles')
    op.drop_table('fiber_bundles')
    op.drop_index('uq_projects_fiber_tenant_id', table_name='projects')
    op.drop_index('uq_devices_fiber_tenant_id', table_name='devices')
    op.drop_index('uq_cables_fiber_tenant_id', table_name='cables')

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
        "otdr_records",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("cable_id", sa.Uuid(), nullable=False),
        sa.Column("strand_id", sa.Uuid(), nullable=True),
        sa.Column("direction", sa.String(length=1), nullable=False),
        sa.Column("wavelength_nm", sa.Integer(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_name", sa.String(length=500), nullable=False),
        sa.Column("source_object_key", sa.String(length=500), nullable=True),
        sa.Column("total_length_m", sa.Float(), nullable=True),
        sa.Column("end_to_end_loss_db", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint("direction IN ('A', 'B')", name="ck_otdr_direction"),
        sa.CheckConstraint(
            "wavelength_nm BETWEEN 600 AND 1700", name="ck_otdr_wavelength"
        ),
        sa.CheckConstraint(
            "total_length_m IS NULL OR total_length_m >= 0", name="ck_otdr_length"
        ),
        sa.CheckConstraint(
            "end_to_end_loss_db IS NULL OR "
            "(end_to_end_loss_db >= 0 AND end_to_end_loss_db <= 100)",
            name="ck_otdr_total_loss",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "cable_id"],
            ["cables.tenant_id", "cables.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "strand_id"],
            ["fiber_strands.tenant_id", "fiber_strands.id"],
            ondelete="RESTRICT",
        ),
        *_tenant_constraints("otdr_records"),
    )
    _tenant_index("otdr_records")
    op.create_table(
        "otdr_events",
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=False),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("loss_db", sa.Float(), nullable=True),
        sa.Column("reflectance_db", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("linked_kind", sa.String(length=32), nullable=True),
        sa.Column("linked_id", sa.Uuid(), nullable=True),
        sa.Column("link_offset_m", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        *_tenant_columns(),
        sa.CheckConstraint(
            "sequence BETWEEN 1 AND 10000", name="ck_otdr_event_sequence"
        ),
        sa.CheckConstraint("distance_m >= 0", name="ck_otdr_event_distance"),
        sa.CheckConstraint(
            "event_type IN ('launch', 'connector', 'splice', 'bend', "
            "'reflective', 'end', 'unknown')",
            name="ck_otdr_event_type",
        ),
        sa.CheckConstraint(
            "loss_db IS NULL OR (loss_db >= 0 AND loss_db <= 100)",
            name="ck_otdr_event_loss",
        ),
        sa.CheckConstraint(
            "reflectance_db IS NULL OR "
            "(reflectance_db >= -120 AND reflectance_db <= 20)",
            name="ck_otdr_event_reflectance",
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_otdr_event_confidence"
        ),
        sa.CheckConstraint(
            "linked_kind IS NULL OR "
            "linked_kind IN ('splice', 'fiber_termination', 'breakout_leg')",
            name="ck_otdr_event_link_kind",
        ),
        sa.CheckConstraint(
            "(linked_kind IS NULL AND linked_id IS NULL) OR "
            "(linked_kind IS NOT NULL AND linked_id IS NOT NULL)",
            name="ck_otdr_event_link_pair",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            ["otdr_records.tenant_id", "otdr_records.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "record_id", "sequence", name="uq_otdr_event_sequence"
        ),
        *_tenant_constraints("otdr_events"),
    )
    _tenant_index("otdr_events")
    _backfill_claims()
    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                "USING (tenant_id = "
                "nullif(current_setting('app.current_tenant', true), '')::uuid) "
                "WITH CHECK (tenant_id = "
                "nullif(current_setting('app.current_tenant', true), '')::uuid)"
            )

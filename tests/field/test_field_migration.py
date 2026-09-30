from __future__ import annotations

import importlib.util
import io
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "apps/api/migrations/versions/200000000007_field_mutation_receipts.py"


def revision():
    spec = importlib.util.spec_from_file_location("field_receipt_revision", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_007_is_frozen_and_chained_after_floorplan():
    module = revision()
    assert module.down_revision == "200000000006"
    assert module.TENANT_TABLES == ["field_mutation_receipts"]
    assert "from app" not in MIGRATION.read_text()


def test_postgresql_offline_sql_forces_tenant_rls():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        revision().upgrade()
    sql = output.getvalue()
    assert 'ALTER TABLE "field_mutation_receipts" ENABLE ROW LEVEL SECURITY' in sql
    assert 'ALTER TABLE "field_mutation_receipts" FORCE ROW LEVEL SECURITY' in sql
    assert 'CREATE POLICY tenant_isolation ON "field_mutation_receipts"' in sql
    assert "UNIQUE (tenant_id, idempotency_key_hash)" in sql
    assert "current_setting('app.current_tenant'" in sql


def test_offline_downgrade_drops_receipt_table_last():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        revision().downgrade()
    sql = output.getvalue()
    assert "DROP TABLE field_mutation_receipts" in sql

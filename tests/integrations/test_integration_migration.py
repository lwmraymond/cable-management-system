from __future__ import annotations

import importlib.util
import io
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.integration_models import INTEGRATION_TABLES

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "apps/api/migrations/versions/200000000008_netbox_signed_webhooks.py"


def revision():
    spec = importlib.util.spec_from_file_location("integration_revision", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_008_is_frozen_and_chained_after_field_queue():
    module = revision()
    assert module.down_revision == "200000000007"
    assert tuple(module.TENANT_TABLES) == INTEGRATION_TABLES
    assert "from app" not in MIGRATION.read_text()


def test_postgresql_offline_sql_forces_rls_on_all_six_tables():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        revision().upgrade()
    sql = output.getvalue()
    for table in INTEGRATION_TABLES:
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in sql
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in sql
        assert f'CREATE POLICY tenant_isolation ON "{table}"' in sql
    assert "UNIQUE (tenant_id, event_id)" in sql
    assert "FOREIGN KEY(tenant_id, endpoint_id)" in sql
    assert "FOREIGN KEY(tenant_id, outbox_id)" in sql


def test_downgrade_drops_children_before_parents():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        revision().downgrade()
    sql = output.getvalue()
    assert sql.index("DROP TABLE webhook_delivery_attempts") < sql.index("DROP TABLE webhook_outbox")
    assert sql.index("DROP TABLE webhook_outbox") < sql.index("DROP TABLE webhook_endpoints")

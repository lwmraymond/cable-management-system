"""Revision 006 gates; these are not a real PostgreSQL ordinary-role proof."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from app.floorplan_editor_models import FLOOR_PLAN_TABLES
from app.models import Base

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "apps/api/migrations/versions/200000000006_floor_plan_editor.py"


def revision():
    spec = importlib.util.spec_from_file_location("floor_plan_revision", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_006_roundtrip_and_metadata_zero_diff(floor_env):
    module = revision()
    assert module.down_revision == "200000000005"
    with floor_env.engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.downgrade()
            assert not set(FLOOR_PLAN_TABLES) & set(inspect(connection).get_table_names())
            module.upgrade()
        assert set(FLOOR_PLAN_TABLES) <= set(inspect(connection).get_table_names())
        assert compare_metadata(context, Base.metadata) == []


def test_postgresql_offline_sql_forces_rls_and_append_only_revisions():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        revision().upgrade()
    sql = output.getvalue()
    for table in FLOOR_PLAN_TABLES:
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in sql
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in sql
        assert f'CREATE POLICY tenant_isolation ON "{table}"' in sql
    assert "floor_plan_revisions_append_only" in sql
    assert "FOREIGN KEY(tenant_id, floor_plan_id)" in sql


def test_frozen_migration_has_no_live_model_imports():
    source = MIGRATION.read_text()
    assert "from app" not in source
    assert revision().TENANT_TABLES == list(FLOOR_PLAN_TABLES)


def test_composition_entrypoint_mounts_router_without_workflows():
    entrypoint = ROOT / "apps/api/app/main_floorplan_editor.py"
    source = entrypoint.read_text()
    assert "build_floorplan_router" in source
    assert "include_router" in source
    assert not (ROOT / ".github/workflows").exists()

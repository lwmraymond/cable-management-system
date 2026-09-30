"""Revision 006 Floor Plan migration gates."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text

from app.floorplan_models import FLOOR_PLAN_TABLES
from app.models import Base

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "apps/api/migrations/versions/200000000006_floor_plan_editor.py"


def revision():
    spec = importlib.util.spec_from_file_location("floor_plan_revision", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pre_revision_engine(path: Path):
    engine = create_engine(
        f"sqlite+pysqlite:///{path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    legacy = [
        table
        for name, table in Base.metadata.tables.items()
        if name not in set(FLOOR_PLAN_TABLES)
    ]
    Base.metadata.create_all(engine, tables=legacy)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX uq_locations_floor_plan_tenant_id"))
    return engine


def test_revision_006_roundtrip_matches_metadata(tmp_path):
    engine = pre_revision_engine(tmp_path / "roundtrip.db")
    module = revision()
    assert module.down_revision == "200000000005"
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
        assert set(FLOOR_PLAN_TABLES) <= set(inspect(connection).get_table_names())
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        with Operations.context(context):
            module.downgrade()
        assert not set(FLOOR_PLAN_TABLES) & set(inspect(connection).get_table_names())
    engine.dispose()


def test_revision_history_is_append_only_on_sqlite(tmp_path):
    engine = pre_revision_engine(tmp_path / "append-only.db")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision().upgrade()
        triggers = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND name LIKE 'floor_plan_revisions_append_only_%'"
            )
        ).scalars().all()
        assert set(triggers) == {
            "floor_plan_revisions_append_only_update",
            "floor_plan_revisions_append_only_delete",
        }
    engine.dispose()


def test_postgresql_offline_sql_forces_rls_and_append_only_trigger():
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
    assert sql.count("WITH CHECK (tenant_id = nullif(current_setting(") == 2
    assert "CREATE TRIGGER floor_plan_revisions_append_only" in sql
    assert "FOREIGN KEY(tenant_id, location_id) REFERENCES locations (tenant_id, id)" in sql
    assert "FOREIGN KEY(tenant_id, project_id) REFERENCES projects (tenant_id, id)" in sql


def test_downgrade_drops_child_before_parent_and_location_index():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        revision().downgrade()
    sql = output.getvalue()
    assert sql.index("DROP TABLE floor_plan_revisions") < sql.index("DROP TABLE floor_plans")
    assert sql.index("DROP TABLE floor_plans") < sql.index(
        "DROP INDEX uq_locations_floor_plan_tenant_id"
    )


def test_revision_is_frozen_and_declares_all_tenant_tables():
    module = revision()
    assert set(module.TENANT_TABLES) == set(FLOOR_PLAN_TABLES)
    assert "from app" not in MIGRATION.read_text()


def test_application_registers_floor_plan_models_and_router():
    for relative in ("apps/api/app/db.py", "apps/api/migrations/env.py"):
        source = (ROOT / relative).read_text()
        assert "from app import floorplan_models" in source
    fiber_api = (ROOT / "apps/api/app/api/fiber.py").read_text()
    assert "from app.api.floorplan import build_floorplan_router" in fiber_api
    assert "root.include_router(build_floorplan_router(get_db, get_principal))" in fiber_api
    main = (ROOT / "apps/api/app/main.py").read_text()
    assert "build_fiber_router(get_db, get_principal)" in main

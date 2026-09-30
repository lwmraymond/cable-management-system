"""Revision 004 gates. These are not an external PostgreSQL runtime proof."""
import ast
import importlib.util
import io
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select

from app.fiber_models import REVISION_004_TABLES, FiberBundle
from app.models import Base, Cable

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "apps/api/migrations/versions/200000000004_fiber_splice_topology.py"


def revision():
    spec = importlib.util.spec_from_file_location("fiber_revision", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_roundtrip_preserves_legacy_rows(env):
    """Use the real legacy model schema, not a mocked connection or recreated domain."""
    module = revision()
    assert module.down_revision == "200000000003"
    with env.engine.begin() as connection:
        count_before = len(connection.execute(select(Cable.id)).all())
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.downgrade()
            assert not set(REVISION_004_TABLES) & set(inspect(connection).get_table_names())
            module.upgrade()
        assert set(REVISION_004_TABLES) <= set(inspect(connection).get_table_names())
        assert len(connection.execute(select(Cable.id)).all()) == count_before
        # Full live metadata now includes revision 005. Its zero-diff gate is
        # executed in test_fiber_advanced_migration.py after both revisions.
    # Actually write through the service after the upgrade.
    bundle = env.write(env.service().provision_bundle, env.cables[0].id, "after-upgrade")
    assert len(bundle["strands"]) == 4
    with env.engine.connect() as connection:
        assert connection.execute(select(FiberBundle.id)).first() is not None


def test_postgresql_offline_sql_enables_and_forces_all_six_tables():
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={
        "as_sql": True, "output_buffer": output,
    })
    with Operations.context(context):
        revision().upgrade()
    sql = output.getvalue()
    for table in REVISION_004_TABLES:
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in sql
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in sql
        assert f'CREATE POLICY tenant_isolation ON "{table}"' in sql
    assert sql.count("WITH CHECK (tenant_id = nullif(current_setting(") == 6
    assert sql.index("CREATE UNIQUE INDEX uq_cables_fiber_tenant_id") < sql.index(
        "CREATE TABLE fiber_bundles")
    assert "UNIQUE (tenant_id, strand_id, side)" in sql
    assert "WHERE deleted_at IS NULL" in sql
    assert "FOREIGN KEY(tenant_id, cable_id) REFERENCES cables (tenant_id, id)" in sql


def test_postgresql_offline_downgrade_orders_children_before_parent_indexes():
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={
        "as_sql": True, "output_buffer": output,
    })
    with Operations.context(context):
        revision().downgrade()
    sql = output.getvalue()
    assert sql.index("DROP TABLE fiber_splice_ends") < sql.index("DROP TABLE fiber_splices")
    assert sql.index("DROP TABLE fiber_bundles") < sql.index("DROP INDEX uq_cables_fiber_tenant_id")


def test_application_mount_uses_the_existing_auth_dependencies():
    source = (ROOT / "apps/api/app/main.py").read_text()
    tree = ast.parse(source)
    mounts = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and node.func.attr == "include_router"]
    assert any(ast.unparse(call.args[0]) == "build_fiber_router(get_db, get_principal)"
               and any(k.arg == "prefix" and ast.unparse(k.value) == "settings.api_prefix"
                       for k in call.keywords) for call in mounts)
    for path in ("apps/api/app/db.py", "apps/api/migrations/env.py"):
        assert "from app import fiber_models" in (ROOT / path).read_text()


def test_frozen_migration_does_not_import_live_models():
    assert set(revision().TENANT_TABLES) == set(REVISION_004_TABLES)
    assert "from app" not in MIGRATION.read_text()

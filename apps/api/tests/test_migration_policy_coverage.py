from __future__ import annotations

import importlib.util
from pathlib import Path

from app import field_models  # noqa: F401 -- register field tables independently of test collection
from app import integration_models  # noqa: F401 -- register connector tables
from app import fiber_models  # noqa: F401 -- include extension tables
from app import floorplan_models  # noqa: F401 -- include Floor Plan tables
from app.models import AuditEvent, Base, TenantOwnedMixin


def load_migration(filename: str = "200000000002_postgresql_rls_and_audit_guard.py"):
    path = Path(__file__).parents[1] / "migrations" / "versions" / filename
    spec = importlib.util.spec_from_file_location("rls_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


def test_postgresql_rls_covers_every_tenant_table() -> None:
    module, _ = load_migration()
    model_tables = {
        mapper.local_table.name
        for mapper in Base.registry.mappers
        if issubclass(mapper.class_, TenantOwnedMixin)
    }
    model_tables.add(AuditEvent.__tablename__)
    fiber, _ = load_migration("200000000004_fiber_splice_topology.py")
    advanced, _ = load_migration("200000000005_advanced_fiber_topology.py")
    floorplan, _ = load_migration("200000000006_floor_plan_editor.py")
    field, _ = load_migration("200000000007_field_mutation_receipts.py")
    integrations, _ = load_migration("200000000008_netbox_signed_webhooks.py")
    assert (
        set(module.TENANT_TABLES)
        | set(fiber.TENANT_TABLES)
        | set(advanced.TENANT_TABLES)
        | set(floorplan.TENANT_TABLES)
        | set(field.TENANT_TABLES)
        | set(integrations.TENANT_TABLES)
    ) == model_tables


def test_postgresql_rls_forces_policy_and_audit_trigger() -> None:
    _, path = load_migration()
    source = path.read_text()
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "CREATE TRIGGER audit_events_append_only" in source
    assert "current_setting('app.current_tenant'" in source


def test_shared_rate_limit_migration() -> None:
    module, path = load_migration("200000000003_shared_rate_limit_windows.py")
    assert module.down_revision == "200000000002"
    source = path.read_text()
    assert "rate_limit_windows" in source
    assert "ix_rate_limit_window_expires" in source


def test_account_workspace_migration_preserves_existing_rows_and_constraints():
    import uuid

    import pytest
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration, _ = load_migration("200000000010_account_workspaces.py")
    engine = sa.create_engine("sqlite+pysqlite://")
    metadata = sa.MetaData()
    sa.Table("user_identities", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    tenants = sa.Table(
        "tenants",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(180), nullable=False),
    )
    metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(tenants.insert().values(id=tenant_id, name="Existing shared workspace"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.execute(
            sa.text("SELECT workspace_kind, workspace_owner_id FROM tenants")
        ).one() == ("shared", None)
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(sa.text("UPDATE tenants SET workspace_kind='personal'"))
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(sa.text("UPDATE tenants SET workspace_kind='public'"))
        migration.downgrade()
        assert (
            connection.execute(sa.select(tenants.c.name)).scalar_one()
            == "Existing shared workspace"
        )
        migration.upgrade()
        assert (
            connection.execute(sa.text("SELECT workspace_kind FROM tenants")).scalar_one()
            == "shared"
        )

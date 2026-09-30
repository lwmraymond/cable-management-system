from __future__ import annotations

import importlib.util
from pathlib import Path

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
    assert (
        set(module.TENANT_TABLES)
        | set(fiber.TENANT_TABLES)
        | set(advanced.TENANT_TABLES)
        | set(floorplan.TENANT_TABLES)
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

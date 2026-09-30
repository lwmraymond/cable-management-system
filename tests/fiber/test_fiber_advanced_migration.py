"""Revision 005 gates; external PostgreSQL execution remains a separate gate."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session

from app.fiber_models import (
    REVISION_004_TABLES,
    REVISION_005_TABLES,
    FiberBundle,
    FiberCassette,
    FiberCassetteSlot,
    FiberEndpointClaim,
    FiberSplice,
    FiberSpliceEnd,
    FiberStrand,
    PhysicalPortClaim,
)
from app.models import (
    Base,
    Cable,
    CableTermination,
    Device,
    Location,
    LocationType,
    Organization,
    OrganizationType,
    Port,
    Project,
    Tenant,
)

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "apps/api/migrations/versions"
MIGRATION_004 = VERSIONS / "200000000004_fiber_splice_topology.py"
MIGRATION_005 = VERSIONS / "200000000005_advanced_fiber_topology.py"
PARENT_INDEXES = (
    "uq_cables_fiber_tenant_id",
    "uq_devices_fiber_tenant_id",
    "uq_projects_fiber_tenant_id",
    "uq_ports_fiber_tenant_id",
    "uq_cable_terminations_fiber_tenant_id",
)


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def revision4_engine(path: Path):
    engine = create_engine(
        f"sqlite+pysqlite:///{path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    extension_tables = set(REVISION_004_TABLES) | set(REVISION_005_TABLES)
    legacy = [table for name, table in Base.metadata.tables.items() if name not in extension_tables]
    Base.metadata.create_all(engine, tables=legacy)
    with engine.begin() as connection:
        for name in PARENT_INDEXES:
            connection.execute(text(f"DROP INDEX {name}"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            load(MIGRATION_004).upgrade()
    return engine


def test_revision_005_roundtrip_and_live_metadata_zero_diff(tmp_path):
    engine = revision4_engine(tmp_path / "roundtrip.db")
    module = load(MIGRATION_005)
    assert module.down_revision == "200000000004"
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
        assert set(REVISION_005_TABLES) <= set(inspect(connection).get_table_names())
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        with Operations.context(context):
            module.downgrade()
        assert not set(REVISION_005_TABLES) & set(inspect(connection).get_table_names())
        assert set(REVISION_004_TABLES) <= set(inspect(connection).get_table_names())
    engine.dispose()


def test_revision_005_backfills_legacy_port_and_splice_claims(tmp_path):
    engine = revision4_engine(tmp_path / "backfill.db")
    with Session(engine, expire_on_commit=False) as session:
        org = Organization(name="Migration customer", organization_type=OrganizationType.CUSTOMER)
        session.add(org)
        session.flush()
        tenant = Tenant(owner_organization_id=org.id, name="Migration", slug="migration")
        session.add(tenant)
        session.flush()
        project = Project(
            tenant_id=tenant.id,
            project_number="M-1",
            name="Migration project",
            customer_organization_id=org.id,
        )
        location = Location(
            tenant_id=tenant.id,
            identifier="M-TR",
            name="Migration room",
            location_type=LocationType.TR,
        )
        session.add_all([project, location])
        session.flush()
        device = Device(
            tenant_id=tenant.id,
            location_id=location.id,
            identifier="M-ODF",
            name="Migration ODF",
            device_type="patch_panel",
        )
        session.add(device)
        session.flush()
        ports = [
            Port(
                tenant_id=tenant.id,
                device_id=device.id,
                identifier=f"P{number}",
                label=f"Port {number}",
                connector_type="LC",
                media_type="fiber_os2",
                position_index=number,
            )
            for number in (1, 2)
        ]
        session.add_all(ports)
        cable = Cable(
            tenant_id=tenant.id,
            project_id=project.id,
            identifier="M-CABLE",
            media_type="fiber_os2",
            construction="trunk",
            strand_count=2,
        )
        session.add(cable)
        session.flush()
        terminations = [
            CableTermination(
                tenant_id=tenant.id,
                cable_id=cable.id,
                side=side,
                port_id=port.id,
            )
            for side, port in zip(("A", "B"), ports)
        ]
        session.add_all(terminations)
        bundle = FiberBundle(
            tenant_id=tenant.id,
            cable_id=cable.id,
            name="M bundle",
            strand_count=2,
        )
        session.add(bundle)
        session.flush()
        strands = [
            FiberStrand(tenant_id=tenant.id, bundle_id=bundle.id, number=number)
            for number in (1, 2)
        ]
        session.add_all(strands)
        cassette = FiberCassette(
            tenant_id=tenant.id,
            device_id=device.id,
            project_id=project.id,
            name="M cassette",
            slot_count=1,
        )
        session.add(cassette)
        session.flush()
        slot = FiberCassetteSlot(
            tenant_id=tenant.id,
            cassette_id=cassette.id,
            number=1,
        )
        session.add(slot)
        session.flush()
        splice = FiberSplice(tenant_id=tenant.id, slot_id=slot.id, loss_db=0.1)
        session.add(splice)
        session.flush()
        ends = [
            FiberSpliceEnd(
                tenant_id=tenant.id,
                splice_id=splice.id,
                strand_id=strand.id,
                side=side,
                end_number=number,
            )
            for number, (strand, side) in enumerate(zip(strands, ("A", "B")), start=1)
        ]
        session.add_all(ends)
        session.commit()
        tenant_id = tenant.id
        port_ids = {port.id for port in ports}
        strand_endpoints = {(end.strand_id, end.side) for end in ends}
        termination_ids = {termination.id for termination in terminations}
        splice_id = splice.id

    module = load(MIGRATION_005)
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
        physical = connection.execute(select(PhysicalPortClaim.__table__)).mappings().all()
        endpoints = connection.execute(select(FiberEndpointClaim.__table__)).mappings().all()
        assert len(physical) == 2
        assert {row["tenant_id"] for row in physical} == {tenant_id}
        assert {row["port_id"] for row in physical} == port_ids
        assert {row["owner_id"] for row in physical} == termination_ids
        assert {row["owner_type"] for row in physical} == {"cable_termination"}
        assert len(endpoints) == 2
        assert {(row["strand_id"], row["side"]) for row in endpoints} == strand_endpoints
        assert {row["owner_id"] for row in endpoints} == {splice_id}
        assert {row["owner_type"] for row in endpoints} == {"splice"}
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()


def test_revision_005_database_constraints_reject_duplicate_active_claims(tmp_path):
    engine = revision4_engine(tmp_path / "constraints.db")
    module = load(MIGRATION_005)
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
    with engine.connect() as connection:
        indexes = {row["name"] for row in inspect(connection).get_indexes("physical_port_claims")}
        endpoint_indexes = {
            row["name"] for row in inspect(connection).get_indexes("fiber_endpoint_claims")
        }
        assert "uq_active_physical_port_claim" in indexes
        assert "uq_fiber_active_endpoint_claim" in endpoint_indexes
    engine.dispose()


def test_postgresql_offline_sql_forces_rls_and_declares_normalized_claims():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        load(MIGRATION_005).upgrade()
    sql = output.getvalue()
    for table in REVISION_005_TABLES:
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in sql
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in sql
        assert f'CREATE POLICY tenant_isolation ON "{table}"' in sql
    assert sql.count("WITH CHECK (tenant_id = nullif(current_setting(") == 10
    assert "CREATE UNIQUE INDEX uq_active_physical_port_claim" in sql
    assert "CREATE UNIQUE INDEX uq_fiber_active_endpoint_claim" in sql
    assert "WHERE deleted_at IS NULL" in sql
    assert "FOREIGN KEY(tenant_id, port_id) REFERENCES ports (tenant_id, id)" in sql
    assert "FOREIGN KEY(tenant_id, strand_id) REFERENCES fiber_strands (tenant_id, id)" in sql


def test_revision_005_downgrade_orders_dependents_before_parent_indexes():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        load(MIGRATION_005).downgrade()
    sql = output.getvalue()
    assert sql.index("DROP TABLE otdr_events") < sql.index("DROP TABLE otdr_records")
    assert sql.index("DROP TABLE channel_members") < sql.index("DROP TABLE copper_pairs")
    assert sql.index("DROP TABLE fiber_breakout_legs") < sql.index("DROP TABLE fiber_breakouts")
    assert sql.index("DROP TABLE physical_port_claims") < sql.index(
        "DROP INDEX uq_ports_fiber_tenant_id"
    )


def test_revision_005_is_frozen_and_application_mounts_advanced_router():
    module = load(MIGRATION_005)
    assert set(module.TENANT_TABLES) == set(REVISION_005_TABLES)
    source = MIGRATION_005.read_text()
    assert "from app" not in source
    main = (ROOT / "apps/api/app/main.py").read_text()
    assert "build_fiber_advanced_router(get_db, get_principal)" in main
    assert "TopologyTraceService(db, principal).trace_cable" in main

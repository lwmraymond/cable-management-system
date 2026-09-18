"""Port reuse preserves history and the existing physical uniqueness boundary."""

from __future__ import annotations

import importlib.util
import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import Cable, CableTermination


def revision():
    path = (
        Path(__file__).parents[1] / "migrations/versions/200000000011_active_cable_terminations.py"
    )
    spec = importlib.util.spec_from_file_location("cable_lifecycle_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def indexes(connection):
    return {row["name"]: row for row in sa.inspect(connection).get_indexes("cable_terminations")}


def test_upgrade_preserves_rows_and_only_active_occupancy_is_unique(world):
    migration = revision()
    cable_table, term_table = Cable.__table__, CableTermination.__table__
    with world.session_factory.kw["bind"].begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.downgrade()  # Model schema -> the actual previous constraint.
        before = (
            connection.execute(sa.select(term_table).order_by(term_table.c.id)).mappings().all()
        )
        migration.upgrade()
        assert (
            connection.execute(sa.select(term_table).order_by(term_table.c.id)).mappings().all()
            == before
        )
        idx = indexes(connection)["uq_active_cable_port_termination"]
        assert idx["unique"] and idx["column_names"] == ["tenant_id", "port_id"]
        assert "deleted_at IS NULL" in str(idx["dialect_options"]["sqlite_where"])
        assert {
            c["name"] for c in sa.inspect(connection).get_unique_constraints("cable_terminations")
        } == {"uq_cable_side"}
        assert len(sa.inspect(connection).get_foreign_keys("cable_terminations")) == 3
        assert (
            sa.inspect(connection).get_check_constraints("cable_terminations")[0]["name"]
            == "ck_cable_termination_side"
        )

        old = dict(before[0])
        cable = dict(
            connection.execute(sa.select(cable_table).where(cable_table.c.id == old["cable_id"]))
            .mappings()
            .one()
        )
        cable.update(id=uuid.uuid4(), identifier="REUSE-TEST-001")
        connection.execute(cable_table.insert().values(**cable))
        replacement = old | {"id": uuid.uuid4(), "cable_id": cable["id"]}
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(term_table.insert().values(**replacement))
        connection.execute(
            term_table.update()
            .where(term_table.c.id == old["id"])
            .values(deleted_at=datetime.now(UTC))
        )
        connection.execute(term_table.insert().values(**replacement))
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(term_table)).scalar_one()
            == len(before) + 1
        )
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(
                term_table.update().where(term_table.c.id == old["id"]).values(deleted_at=None)
            )
        with pytest.raises(RuntimeError, match="historical cable port reuse"):
            migration.downgrade()
        assert "uq_active_cable_port_termination" in indexes(connection)
        assert connection.execute(sa.text("PRAGMA foreign_key_check")).all() == []


def test_roundtrip_without_port_reuse_keeps_all_history(world):
    migration = revision()
    with world.session_factory.kw["bind"].begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        before = connection.execute(sa.select(CableTermination.__table__)).all()
        migration.downgrade()
        assert "uq_active_cable_port_termination" not in indexes(connection)
        migration.upgrade()
        assert connection.execute(sa.select(CableTermination.__table__)).all() == before


def test_postgresql_upgrade_uses_partial_index_without_recreating_rls_table():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        revision().upgrade()
    sql = output.getvalue()
    assert "DROP CONSTRAINT uq_physical_port_termination" in sql
    assert "CREATE UNIQUE INDEX uq_active_cable_port_termination" in sql
    assert "WHERE deleted_at IS NULL" in sql
    assert "DROP TABLE" not in sql
    assert "DISABLE ROW LEVEL SECURITY" not in sql


def test_postgresql_downgrade_guards_history_before_schema_change():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        revision().downgrade()
    sql = output.getvalue()
    assert sql.index("RAISE EXCEPTION") < sql.index("DROP INDEX")
    assert "UNIQUE (tenant_id, port_id)" in sql

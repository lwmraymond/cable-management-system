from __future__ import annotations
import importlib.util
import io
from pathlib import Path
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def revision():
    path = Path(__file__).parents[1] / "migrations/versions/200000000012_cable_route_portions.py"
    spec = importlib.util.spec_from_file_location("route_portions_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_keeps_legacy_rows_and_refuses_destructive_downgrade():
    engine = sa.create_engine("sqlite+pysqlite://")
    migration = revision()
    with engine.begin() as db:
        db.execute(
            sa.text(
                "CREATE TABLE cable_route_segments (id TEXT PRIMARY KEY, cable_id TEXT NOT NULL, sequence INTEGER NOT NULL, UNIQUE(cable_id,sequence))"
            )
        )
        db.execute(sa.text("INSERT INTO cable_route_segments VALUES ('legacy','cable',1)"))
        migration.op = Operations(MigrationContext.configure(db))
        migration.upgrade()
        assert db.execute(sa.text("SELECT * FROM cable_route_segments")).one() == (
            "legacy",
            "cable",
            1,
            None,
            None,
            None,
        )
        migration.downgrade()
        assert db.execute(sa.text("SELECT * FROM cable_route_segments")).one() == (
            "legacy",
            "cable",
            1,
        )
        migration.upgrade()
        with pytest.raises(sa.exc.IntegrityError), db.begin_nested():
            db.execute(sa.text("UPDATE cable_route_segments SET start_offset_m=1"))
        db.execute(
            sa.text(
                "UPDATE cable_route_segments SET start_offset_m=1,end_offset_m=2,geometry_hash='hash'"
            )
        )
        with pytest.raises(RuntimeError, match="partial cable routes exist"):
            migration.downgrade()
        assert db.execute(
            sa.text("SELECT start_offset_m,end_offset_m FROM cable_route_segments")
        ).one() == (1, 2)
        assert sa.inspect(db).get_unique_constraints("cable_route_segments")[0]["column_names"] == [
            "cable_id",
            "sequence",
        ]


def test_postgresql_offline_upgrade_and_downgrade_guard():
    output = io.StringIO()
    migration = revision()
    migration.op = Operations(
        MigrationContext.configure(
            dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
        )
    )
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()
    assert "ADD COLUMN start_offset_m" in sql
    assert "ck_cable_route_portion" in sql
    assert sql.index("partial cable routes exist") < sql.index("DROP COLUMN start_offset_m")

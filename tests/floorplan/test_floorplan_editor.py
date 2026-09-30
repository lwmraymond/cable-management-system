from __future__ import annotations

import copy
import uuid

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.floorplan_editor_models import FloorPlan, FloorPlanRevision


def create_plan(env, name="Main"):
    return env.write(
        env.service().create_plan,
        project_id=env.project.id,
        location_id=env.floor.id,
        name=name,
        width_mm=30_000.0,
        height_mm=20_000.0,
        grid_mm=500.0,
    )


def document(plan, objects=None):
    return {
        "schema_version": 1,
        "unit": "mm",
        "canvas": {
            "width_mm": plan["width_mm"],
            "height_mm": plan["height_mm"],
            "grid_mm": 500.0,
            "snap": True,
        },
        "objects": objects or [],
    }


def rack_object(env, *, resource=None, x=1000.0, client="rack-1"):
    return {
        "client_id": client,
        "kind": "rack",
        "resource_id": str(resource or env.rack.id),
        "label": "Rack 1",
        "x_mm": x,
        "y_mm": 1000.0,
        "width_mm": 600.0,
        "height_mm": 1000.0,
        "rotation_deg": 0.0,
        "z_index": 1,
        "geometry": {},
    }


def test_create_persists_initial_immutable_revision_and_audit(floor_env):
    plan = create_plan(floor_env)
    assert plan["version"] == 1
    assert plan["head_revision"] == 1
    assert plan["revision"] == 1
    assert len(plan["checksum_sha256"]) == 64
    assert floor_env.db.scalar(select(func.count()).select_from(FloorPlan)) == 1
    assert floor_env.db.scalar(select(func.count()).select_from(FloorPlanRevision)) == 1


def test_save_normalizes_sorting_and_advances_version(floor_env):
    plan = create_plan(floor_env)
    items = [
        {**rack_object(floor_env, client="b", x=2500), "z_index": 5},
        {
            "client_id": "a",
            "kind": "annotation",
            "resource_id": None,
            "label": "Note",
            "x_mm": 500,
            "y_mm": 500,
            "width_mm": 1000,
            "height_mm": 500,
            "rotation_deg": 0,
            "z_index": 1,
            "geometry": {"text": "A"},
        },
    ]
    saved = floor_env.write(
        floor_env.service().save_revision,
        uuid.UUID(plan["id"]),
        1,
        document(plan, items),
        "Placed rack",
    )
    assert saved["version"] == 2 and saved["revision"] == 2
    loaded = floor_env.service().get_plan(uuid.UUID(plan["id"]))
    assert [item["client_id"] for item in loaded["document"]["objects"]] == ["a", "b"]
    assert loaded["checksum_sha256"] == saved["checksum_sha256"]


def test_stale_revision_save_is_rejected_without_partial_row(floor_env):
    plan = create_plan(floor_env)
    floor_env.write(
        floor_env.service().save_revision,
        uuid.UUID(plan["id"]),
        1,
        document(plan, [rack_object(floor_env)]),
    )
    with pytest.raises(ConflictError, match="changed"):
        floor_env.write(
            floor_env.service().save_revision,
            uuid.UUID(plan["id"]),
            1,
            document(plan),
        )
    assert floor_env.db.scalar(select(func.count()).select_from(FloorPlanRevision)) == 2


def test_publish_and_published_view_are_stable(floor_env):
    plan = create_plan(floor_env)
    floor_env.write(
        floor_env.service().save_revision,
        uuid.UUID(plan["id"]),
        1,
        document(plan, [rack_object(floor_env)]),
    )
    published = floor_env.write(
        floor_env.service().publish,
        uuid.UUID(plan["id"]),
        2,
        2,
    )
    assert published["version"] == 3 and published["published_revision"] == 2
    floor_env.write(
        floor_env.service().save_revision,
        uuid.UUID(plan["id"]),
        3,
        document(plan),
    )
    working = floor_env.service().get_plan(uuid.UUID(plan["id"]), "working")
    public = floor_env.service().get_plan(uuid.UUID(plan["id"]), "published")
    assert working["revision"] == 3 and not working["document"]["objects"]
    assert public["revision"] == 2 and len(public["document"]["objects"]) == 1


def test_restore_creates_a_new_revision_instead_of_mutating_history(floor_env):
    plan = create_plan(floor_env)
    first_checksum = plan["checksum_sha256"]
    floor_env.write(
        floor_env.service().save_revision,
        uuid.UUID(plan["id"]),
        1,
        document(plan, [rack_object(floor_env)]),
    )
    restored = floor_env.write(
        floor_env.service().restore,
        uuid.UUID(plan["id"]),
        1,
        2,
        "Rollback",
    )
    assert restored["revision"] == 3 and restored["restored_from_revision"] == 1
    assert restored["checksum_sha256"] == first_checksum
    history = floor_env.service().list_revisions(uuid.UUID(plan["id"]))["items"]
    assert [row["revision"] for row in history] == [3, 2, 1]


def test_missing_published_revision_returns_404_boundary(floor_env):
    plan = create_plan(floor_env)
    with pytest.raises(NotFoundError, match="published"):
        floor_env.service().get_plan(uuid.UUID(plan["id"]), "published")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda doc: doc.update(schema_version=2),
        lambda doc: doc.update(unit="m"),
        lambda doc: doc.update(objects="bad"),
        lambda doc: doc["canvas"].update(width_mm=1),
        lambda doc: doc["canvas"].update(snap="yes"),
    ],
)
def test_invalid_document_contract_is_rejected(floor_env, mutation):
    plan = create_plan(floor_env)
    value = document(plan)
    mutation(value)
    with pytest.raises(ValidationError):
        floor_env.write(
            floor_env.service().save_revision,
            uuid.UUID(plan["id"]),
            1,
            value,
        )


def test_duplicate_client_ids_and_nonfinite_geometry_are_rejected(floor_env):
    plan = create_plan(floor_env)
    duplicate = rack_object(floor_env)
    with pytest.raises(ValidationError, match="Duplicate"):
        floor_env.write(
            floor_env.service().save_revision,
            uuid.UUID(plan["id"]),
            1,
            document(plan, [duplicate, copy.deepcopy(duplicate)]),
        )
    bad = rack_object(floor_env)
    bad["x_mm"] = float("nan")
    with pytest.raises(ValidationError):
        floor_env.write(
            floor_env.service().save_revision,
            uuid.UUID(plan["id"]),
            1,
            document(plan, [bad]),
        )


def test_resource_must_be_in_floor_location_subtree(floor_env):
    plan = create_plan(floor_env)
    with pytest.raises(AuthorizationError, match="outside"):
        floor_env.write(
            floor_env.service().save_revision,
            uuid.UUID(plan["id"]),
            1,
            document(plan, [rack_object(floor_env, resource=floor_env.outside_rack.id)]),
        )


def test_cross_tenant_parents_and_guessed_ids_are_not_visible(floor_env):
    with pytest.raises(NotFoundError):
        floor_env.write(
            floor_env.service().create_plan,
            project_id=floor_env.other_project.id,
            location_id=floor_env.floor.id,
            name="Foreign",
            width_mm=1000,
            height_mm=1000,
        )
    with pytest.raises(NotFoundError):
        floor_env.service().get_plan(uuid.uuid4())


def test_reader_can_read_but_cannot_write_with_forged_cached_permissions(floor_env):
    plan = create_plan(floor_env)
    reader = floor_env.service(floor_env.reader)
    assert reader.get_plan(uuid.UUID(plan["id"]))["revision"] == 1
    with pytest.raises(AuthorizationError):
        floor_env.write(
            reader.save_revision,
            uuid.UUID(plan["id"]),
            1,
            document(plan),
        )


def test_duplicate_name_uses_database_constraint(floor_env):
    create_plan(floor_env)
    with pytest.raises(IntegrityError):
        create_plan(floor_env)
    assert floor_env.db.scalar(select(func.count()).select_from(FloorPlan)) == 1


def test_raw_cross_tenant_revision_foreign_key_is_rejected(floor_env):
    plan = create_plan(floor_env)
    with pytest.raises(IntegrityError):
        floor_env.db.execute(
            insert(FloorPlanRevision.__table__).values(
                tenant_id=floor_env.other_tenant.id,
                floor_plan_id=uuid.UUID(plan["id"]),
                revision=9,
                schema_version=1,
                document=document(plan),
                checksum_sha256="0" * 64,
                note="foreign",
                created_by=floor_env.owner.id,
                version=1,
            )
        )
    floor_env.db.rollback()


def test_list_is_scope_filtered_and_bounded(floor_env):
    create_plan(floor_env, "B")
    create_plan(floor_env, "A")
    listed = floor_env.service().list_plans(floor_env.project.id, floor_env.floor.id, 1)
    assert listed["truncated"] is True
    assert listed["items"][0]["name"] == "A"


def test_platform_bypass_and_wrong_tenant_sessions_are_rejected(floor_env):
    floor_env.db.info["bypass_tenant"] = True
    with pytest.raises(AuthorizationError):
        floor_env.service()
    floor_env.db.info["bypass_tenant"] = False
    floor_env.db.info["tenant_id"] = floor_env.other_tenant.id
    with pytest.raises(AuthorizationError):
        floor_env.service()

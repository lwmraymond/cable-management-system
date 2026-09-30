from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import uuid

import pytest
from sqlalchemy import func, select

from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.floorplan_models import FloorPlan, FloorPlanRevision
from app.models import AuditEvent


def empty_document():
    return {"schema_version": 1, "grid_size": 10, "objects": [], "paths": []}


def object_document(env):
    return {
        "schema_version": 1,
        "grid_size": 25,
        "objects": [
            {
                "id": "rack-1",
                "object_type": "rack",
                "object_id": str(env.racks[0].id),
                "x": 100,
                "y": 200,
                "width": 60,
                "height": 100,
                "rotation": 0,
                "z_index": 1,
                "locked": False,
                "label": "Rack A",
            },
            {
                "id": "device-1",
                "object_type": "device",
                "object_id": str(env.devices[0].id),
                "x": 200,
                "y": 250,
                "width": 50,
                "height": 30,
                "rotation": 90,
                "z_index": 2,
                "locked": True,
                "label": "Patch panel",
            },
        ],
        "paths": [
            {
                "id": "path-1",
                "pathway_id": str(env.pathways[0].id),
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 100, "y": 100},
                    {"x": 300, "y": 100},
                ],
                "width": 10,
                "label": "Tray A",
            }
        ],
    }


def create_plan(env, actor=None, document=None):
    service = env.service(actor)
    return env.write(
        service.create_plan,
        project_id=env.projects[0].id,
        location_id=env.floors[0].id,
        name="Floor 1",
        units="mm",
        canvas_width=1000,
        canvas_height=800,
        background_reference="tenant/floor-1/background.svg",
        document=document or empty_document(),
    )


def count(env, model):
    return env.db.scalar(select(func.count()).select_from(model))


def test_create_plan_persists_initial_revision_checksum_and_audit(env):
    result = create_plan(env, document=object_document(env))
    assert result["version"] == 1
    assert result["current_revision_number"] == 1
    assert result["status"] == "draft"
    assert len(result["current_revision"]["checksum_sha256"]) == 64
    assert result["document"]["objects"][0]["id"] == "rack-1"
    assert count(env, FloorPlan) == 1
    assert count(env, FloorPlanRevision) == 1
    audit = env.db.scalar(select(AuditEvent).where(AuditEvent.action == "floorplan.created"))
    assert audit.after["revision"] == 1


def test_save_publish_restore_are_append_only_and_versioned(env):
    created = create_plan(env)
    plan_id = uuid.UUID(created["id"])
    service = env.service()

    revision_two = env.write(
        service.save_revision,
        plan_id,
        expected_version=1,
        document=object_document(env),
        change_summary="Place equipment and pathway",
    )
    assert revision_two["version"] == 2
    assert revision_two["current_revision_number"] == 2
    assert revision_two["status"] == "draft"

    published = env.write(service.publish, plan_id, expected_version=2)
    assert published["version"] == 3
    assert published["status"] == "published"
    assert published["published_revision_number"] == 2
    assert published["published_at"]

    history = service.revisions(plan_id)
    assert [row["revision_number"] for row in history["items"]] == [2, 1]
    first_revision_id = uuid.UUID(history["items"][1]["id"])
    restored = env.write(
        service.restore,
        plan_id,
        first_revision_id,
        expected_version=3,
    )
    assert restored["version"] == 4
    assert restored["current_revision_number"] == 3
    assert restored["status"] == "draft"
    assert restored["published_revision_number"] == 2
    assert restored["document"]["objects"] == []
    assert restored["current_revision"]["restored_from_revision_id"] == str(first_revision_id)

    rows = env.db.scalars(
        select(FloorPlanRevision)
        .where(FloorPlanRevision.floor_plan_id == plan_id)
        .order_by(FloorPlanRevision.revision_number)
    ).all()
    assert len(rows) == 3
    assert rows[1].document["objects"][0]["id"] == "rack-1"
    assert rows[2].checksum_sha256 == rows[0].checksum_sha256


def test_stale_versions_and_unchanged_document_are_rejected_without_partial_history(env):
    created = create_plan(env)
    plan_id = uuid.UUID(created["id"])
    service = env.service()
    with pytest.raises(ConflictError, match="unchanged"):
        env.write(
            service.save_revision,
            plan_id,
            expected_version=1,
            document=empty_document(),
        )
    assert count(env, FloorPlanRevision) == 1

    changed = object_document(env)
    saved = env.write(
        service.save_revision,
        plan_id,
        expected_version=1,
        document=changed,
    )
    assert saved["version"] == 2
    with pytest.raises(ConflictError, match="changed"):
        env.write(
            service.save_revision,
            plan_id,
            expected_version=1,
            document={**changed, "grid_size": 50},
        )
    with pytest.raises(ConflictError, match="changed"):
        env.write(service.publish, plan_id, expected_version=1)
    assert count(env, FloorPlanRevision) == 2
    assert env.service().get_plan(plan_id)["version"] == 2


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda document: document.update(schema_version=2), "schema_version"),
        (lambda document: document.update(grid_size=True), "grid_size"),
        (
            lambda document: document["objects"][0].update(x=999),
            "exceeds",
        ),
        (
            lambda document: document["objects"].append(
                deepcopy(document["objects"][0])
            ),
            "Duplicate",
        ),
        (
            lambda document: document["paths"][0]["points"].reverse(),
            None,
        ),
    ],
)
def test_document_validation_rejects_invalid_schema_bounds_and_duplicates(env, mutator, match):
    document = object_document(env)
    mutator(document)
    if match is None:
        result = create_plan(env, document=document)
        assert result["document"]["paths"][0]["points"][0]["x"] == 300
        return
    with pytest.raises((ValidationError, AuthorizationError), match=match):
        create_plan(env, document=document)


def test_document_normalization_is_deterministic_and_checksum_is_canonical(env):
    first = object_document(env)
    second = deepcopy(first)
    second["objects"].reverse()
    created = create_plan(env, document=first)
    plan_id = uuid.UUID(created["id"])
    with pytest.raises(ConflictError, match="unchanged"):
        env.write(
            env.service().save_revision,
            plan_id,
            expected_version=1,
            document=second,
        )


def test_out_of_scope_and_cross_tenant_resources_are_not_accepted(env):
    document = object_document(env)
    document["objects"][0]["object_id"] = str(env.racks[1].id)
    with pytest.raises((NotFoundError, AuthorizationError)):
        create_plan(env, document=document)

    service = env.service()
    with pytest.raises(NotFoundError):
        env.write(
            service.create_plan,
            project_id=env.projects[1].id,
            location_id=env.floors[0].id,
            name="Foreign",
            units="mm",
            canvas_width=100,
            canvas_height=100,
        )


def test_reader_can_read_but_cannot_write_despite_forged_cached_permissions(env):
    created = create_plan(env)
    plan_id = uuid.UUID(created["id"])
    reader = env.service(env.reader)
    assert reader.get_plan(plan_id)["id"] == created["id"]
    with pytest.raises(AuthorizationError):
        env.write(
            reader.save_revision,
            plan_id,
            expected_version=1,
            document=object_document(env),
        )
    with pytest.raises(AuthorizationError):
        env.write(reader.publish, plan_id, expected_version=1)


def test_contractor_scope_and_expiry_are_rechecked(env):
    created = create_plan(env, actor=env.contractor)
    plan_id = uuid.UUID(created["id"])
    contractor = env.service(env.contractor)
    assert contractor.get_plan(plan_id)["id"] == created["id"]
    with pytest.raises(AuthorizationError):
        env.write(contractor.publish, plan_id, expected_version=1)

    env.grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    env.db.commit()
    with pytest.raises(AuthorizationError):
        contractor.get_plan(plan_id)


def test_list_is_scoped_sorted_and_bounded(env):
    create_plan(env)
    env.write(
        env.service().create_plan,
        project_id=env.projects[0].id,
        location_id=env.floors[0].id,
        name="Another",
        units="m",
        canvas_width=100,
        canvas_height=100,
    )
    result = env.service().list_plans(
        project_id=env.projects[0].id,
        location_id=env.floors[0].id,
        limit=1,
    )
    assert result["truncated"]
    assert result["items"][0]["name"] == "Another"
    with pytest.raises(ValidationError):
        env.service().list_plans(
            project_id=env.projects[0].id,
            location_id=env.floors[0].id,
            limit=True,
        )


def test_restore_rejects_revision_from_another_plan(env):
    first = create_plan(env)
    second = env.write(
        env.service().create_plan,
        project_id=env.projects[0].id,
        location_id=env.floors[0].id,
        name="Second",
        units="mm",
        canvas_width=500,
        canvas_height=500,
    )
    source = env.service().revisions(uuid.UUID(second["id"]))["items"][0]
    with pytest.raises(NotFoundError, match="does not belong"):
        env.write(
            env.service().restore,
            uuid.UUID(first["id"]),
            uuid.UUID(source["id"]),
            expected_version=1,
        )


def test_platform_bypass_and_wrong_tenant_session_are_denied(env):
    env.db.info["bypass_tenant"] = True
    with pytest.raises(AuthorizationError):
        env.service()
    env.db.info["bypass_tenant"] = False
    env.db.info["tenant_id"] = env.tenants[1].id
    with pytest.raises(AuthorizationError):
        env.service()

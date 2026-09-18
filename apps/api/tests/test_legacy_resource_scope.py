"""Regression coverage for object scope on the legacy API and cable workflow."""

from datetime import UTC, datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import deps
from app.config import Settings
from app.main import app
from app.models import (
    AccessGrant,
    AccessGrantStatus,
    AuditEvent,
    Cable,
    CableStatus,
    CableRouteSegment,
    Device,
    Pathway,
    PathwaySegment,
    Port,
    Project,
    Rack,
    Tenant,
    TestRecord as CableTestRecord,
    WorkOrder,
)


@pytest.fixture
def scoped_world(world, monkeypatch):
    # Each test is an independent server; do not share its IP quota with earlier tests.
    monkeypatch.setattr(app, "middleware_stack", None)
    monkeypatch.setattr(deps, "SessionLocal", world.session_factory)
    monkeypatch.setattr(deps, "PlatformSessionLocal", world.session_factory)
    settings = Settings(_env_file=None, auth_mode="demo", demo_mode=True, rate_limit_enabled=False)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    with world.scoped_session() as db:
        tenant = db.get(Tenant, world.tenant_a)
        other_project = Project(
            tenant_id=tenant.id,
            project_number="OUTSIDE",
            name="Other project",
            customer_organization_id=tenant.owner_organization_id,
        )
        rack = Rack(
            tenant_id=tenant.id,
            location_id=world.other_building,
            rack_identifier="OUTSIDE-RACK",
            name="Other building rack",
        )
        db.add_all([other_project, rack])
        db.flush()
        device = Device(
            tenant_id=tenant.id,
            rack_id=rack.id,
            location_id=world.other_building,
            identifier="OUTSIDE-DEVICE",
            name="Other device",
            device_type="switch",
        )
        cable = Cable(
            tenant_id=tenant.id,
            project_id=other_project.id,
            identifier="000-OUTSIDE",
            media_type="copper",
            construction="horizontal_cable",
        )
        pathway = Pathway(
            tenant_id=tenant.id,
            location_id=world.other_building,
            identifier="OUTSIDE-TRAY",
            name="Other tray",
            pathway_type="tray",
        )
        db.add_all([device, cable, pathway])
        db.flush()
        port = Port(
            tenant_id=tenant.id,
            device_id=device.id,
            identifier="P1",
            label="P1",
            connector_type="RJ45",
            media_type="copper",
            position_index=1,
        )
        order = WorkOrder(
            tenant_id=tenant.id,
            project_id=world.project,
            location_id=world.other_building,
            work_order_number="OUTSIDE-WO",
            title="Other building work",
            assigned_organization_id=tenant.owner_organization_id,
            created_by=world.admin,
        )
        record = CableTestRecord(
            tenant_id=tenant.id,
            cable_id=cable.id,
            tester_id=world.admin,
            result="PASS",
            measurements={},
            tested_at=datetime.now(UTC),
        )
        db.add_all([port, order, record])
        db.flush()
        grant = db.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        grant.permissions = grant.permissions + ["location:read", "pathway:read", "cable:approve"]
        db.commit()
        ids = {
            "rack": rack.id,
            "device": device.id,
            "cable": cable.id,
            "pathway": pathway.id,
            "order": order.id,
            "record": record.id,
            "project": other_project.id,
        }
    return world, TestClient(app), ids


def headers(world, actor=None):
    return {
        "X-Tenant-ID": str(world.tenant_a),
        "X-Actor-ID": str(actor or world.contractor),
        "X-Project-ID": str(world.project),
        "X-Location-ID": str(world.building),
    }


@pytest.mark.parametrize(
    ("endpoint", "key"),
    [
        ("racks", "rack"),
        ("devices", "device"),
        ("cables", "cable"),
        ("pathways", "pathway"),
        ("work-orders", "order"),
        ("test-results", "record"),
    ],
)
def test_collections_filter_actual_scope_and_preserve_admin_access(scoped_world, endpoint, key):
    world, client, ids = scoped_world
    response = client.get(f"/api/v1/{endpoint}", headers=headers(world))
    assert response.status_code == 200, response.text
    assert str(ids[key]) not in {row["id"] for row in response.json()}
    owner = client.get(f"/api/v1/{endpoint}", headers=headers(world, world.admin))
    assert str(ids[key]) in {row["id"] for row in owner.json()}


def test_inventory_direct_queries_and_locations_cannot_escape_scope(scoped_world):
    world, client, ids = scoped_world
    locations = client.get("/api/v1/locations", headers=headers(world)).json()
    assert str(world.tr) in {row["id"] for row in locations}
    assert str(world.other_building) not in {row["id"] for row in locations}
    assert (
        client.get(f"/api/v1/racks/{ids['rack']}/elevation", headers=headers(world)).status_code
        == 403
    )
    assert (
        client.get(f"/api/v1/ports?device_id={ids['device']}", headers=headers(world)).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/racks?location_id={world.other_building}", headers=headers(world)
        ).json()
        == []
    )
    assert (
        client.get(f"/api/v1/racks/{world.rack}/elevation", headers=headers(world)).status_code
        == 200
    )


def test_cable_pagination_applies_after_authorization(scoped_world):
    world, client, _ = scoped_world
    first = client.get("/api/v1/cables?limit=1&offset=0", headers=headers(world)).json()
    second = client.get("/api/v1/cables?limit=1&offset=1", headers=headers(world)).json()
    assert {first[0]["id"], second[0]["id"]} == {str(world.horizontal_cable), str(world.patch_cord)}
    assert client.get("/api/v1/cables?limit=1&offset=2", headers=headers(world)).json() == []


@pytest.mark.parametrize("operation", ["install", "test", "approve"])
def test_workflow_rejects_actual_project_outside_grant_without_mutation(scoped_world, operation):
    world, client, ids = scoped_world
    if operation == "install":
        response = client.post(f"/api/v1/cables/{ids['cable']}/install", headers=headers(world))
    elif operation == "test":
        response = client.post(
            f"/api/v1/cables/{ids['cable']}/tests", headers=headers(world), json={"result": "PASS"}
        )
    else:
        response = client.post(f"/api/v1/tests/{ids['record']}/approve", headers=headers(world))
    assert response.status_code == 403, response.text
    with world.scoped_session() as db:
        assert db.get(Cable, ids["cable"]).installation_status == CableStatus.PLANNED
        assert db.get(CableTestRecord, ids["record"]).status != "approved"
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.object_id == ids["cable"])) is None


def test_permissions_cannot_be_borrowed_from_another_scope(scoped_world):
    world, client, ids = scoped_world
    with world.scoped_session() as db:
        db.add(
            AccessGrant(
                tenant_id=world.tenant_a,
                subject_user_id=world.contractor,
                subject_organization_id=world.contractor_org,
                project_id=ids["project"],
                permissions=["cable:read"],
                approved_by=world.admin,
                status=AccessGrantStatus.ACTIVE,
            )
        )
        db.commit()
    visible = client.get("/api/v1/cables", headers=headers(world)).json()
    assert str(ids["cable"]) in {row["id"] for row in visible}
    response = client.post(f"/api/v1/cables/{ids['cable']}/install", headers=headers(world))
    assert response.status_code == 403


def test_linked_work_order_outside_location_is_rejected_before_install(scoped_world):
    world, client, ids = scoped_world
    response = client.post(
        f"/api/v1/cables/{world.horizontal_cable}/install?work_order_id={ids['order']}",
        headers=headers(world),
    )
    assert response.status_code == 403
    with world.scoped_session() as db:
        assert db.get(Cable, world.horizontal_cable).installation_status == CableStatus.PLANNED


@pytest.mark.parametrize("later_result", ["PASS", "FAIL"])
def test_superseded_passing_test_cannot_commission_cable(scoped_world, later_result):
    world, client, _ = scoped_world
    assert (
        client.post(
            f"/api/v1/cables/{world.horizontal_cable}/install", headers=headers(world)
        ).status_code
        == 200
    )
    first = client.post(
        f"/api/v1/cables/{world.horizontal_cable}/tests",
        headers=headers(world),
        json={"result": "PASS"},
    )
    later = client.post(
        f"/api/v1/cables/{world.horizontal_cable}/tests",
        headers=headers(world),
        json={"result": later_result},
    )
    assert first.status_code == later.status_code == 201
    response = client.post(
        f"/api/v1/tests/{first.json()['id']}/approve", headers=headers(world, world.supervisor)
    )
    assert response.status_code == 409
    assert "superseded" in response.json()["detail"]
    with world.scoped_session() as db:
        cable = db.get(Cable, world.horizontal_cable)
        assert cable.installation_status == CableStatus.TESTED
        assert cable.test_status == later_result
        assert db.get(CableTestRecord, uuid.UUID(first.json()["id"])).status != "approved"
    if later_result == "PASS":
        for _ in range(2):
            assert (
                client.post(
                    f"/api/v1/tests/{later.json()['id']}/approve",
                    headers=headers(world, world.supervisor),
                ).status_code
                == 200
            )
        with world.scoped_session() as db:
            assert (
                len(
                    db.scalars(
                        select(AuditEvent).where(
                            AuditEvent.object_id == world.horizontal_cable,
                            AuditEvent.action == "cable.commissioned",
                        )
                    ).all()
                )
                == 1
            )


@pytest.mark.parametrize("missing", ["endpoint", "route"])
def test_unresolvable_live_cable_links_do_not_expand_room_grants(scoped_world, missing):
    world, client, ids = scoped_world
    with world.scoped_session() as db:
        if missing == "endpoint":
            db.get(Device, world.outlet).deleted_at = datetime.now(UTC)
        else:
            segment = PathwaySegment(
                tenant_id=world.tenant_a,
                pathway_id=ids["pathway"],
                sequence=1,
                name="Outside route",
                length_m=1,
            )
            db.add(segment)
            db.flush()
            db.add(
                CableRouteSegment(
                    tenant_id=world.tenant_a,
                    cable_id=world.horizontal_cable,
                    pathway_segment_id=segment.id,
                    sequence=1,
                )
            )
            db.get(Pathway, ids["pathway"]).deleted_at = datetime.now(UTC)
        db.commit()
    visible = client.get("/api/v1/cables", headers=headers(world)).json()
    assert str(world.horizontal_cable) not in {row["id"] for row in visible}
    assert (
        client.post(
            f"/api/v1/cables/{world.horizontal_cable}/install", headers=headers(world)
        ).status_code
        == 403
    )

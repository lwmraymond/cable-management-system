"""Account isolation for reporting, delegation and legacy work-order creation."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.exceptions import AuthorizationError
from app.models import (
    AccessGrant,
    AccessGrantStatus,
    Device,
    DeviceTemplate,
    Location,
    Project,
    Tenant,
    TenantMembership,
    TestRecord as CableTestRecord,
    WorkOrder,
)
from app.security import resolve_principal
from app.services.compliance import ComplianceService
from app.services.reporting import ReportingService
from test_legacy_resource_scope import scoped_world, headers  # noqa: F401 -- shared HTTP fixture


@pytest.fixture
def endpoint_world(scoped_world):  # noqa: F811 -- pytest fixture injection
    world, client, ids = scoped_world
    with world.scoped_session() as db:
        grant = db.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        grant.permissions = grant.permissions + [
            "dashboard:read",
            "search:read",
            "compliance:read",
            "report:export",
            "audit:read",
            "work_order:create",
            "access_grant:create",
            "access_grant:revoke",
            "device:create",
        ]
        hidden_template = DeviceTemplate(
            tenant_id=world.tenant_a,
            manufacturer="SECRET",
            model="OTHER-ROOM",
            device_type="switch",
            rack_units=1,
            port_blueprint=[],
        )
        db.add(hidden_template)
        db.flush()
        db.get(Device, ids["device"]).template_id = hidden_template.id
        db.get(CableTestRecord, ids["record"]).result = "FAIL"
        db.add(
            CableTestRecord(
                tenant_id=world.tenant_a,
                cable_id=world.horizontal_cable,
                tester_id=world.admin,
                result="FAIL",
                measurements={},
                tested_at=datetime.now(UTC),
            )
        )
        db.commit()
        ids.update(grant=grant.id, template=hidden_template.id)
    with world.session_factory() as db:
        db.info["bypass_tenant"] = True
        other = db.get(Tenant, world.tenant_b)
        project = Project(
            tenant_id=other.id,
            project_number="FOREIGN-P",
            name="Foreign project",
            customer_organization_id=other.owner_organization_id,
        )
        db.add(project)
        db.flush()
        location = db.scalar(select(Location).where(Location.tenant_id == other.id))
        grant = AccessGrant(
            tenant_id=other.id,
            subject_user_id=world.contractor,
            subject_organization_id=world.contractor_org,
            project_id=project.id,
            location_id=location.id,
            permissions=["cable:read"],
            approved_by=world.admin,
            status=AccessGrantStatus.ACTIVE,
        )
        db.add(grant)
        db.commit()
        ids.update(foreign_project=project.id, foreign_location=location.id, foreign_grant=grant.id)
    return world, client, ids


def order_payload(world, **changes):
    return {
        "project_id": str(world.project),
        "location_id": str(world.tr),
        "cable_id": str(world.horizontal_cable),
        "work_order_number": "SAFE-WO",
        "title": "Authorized job",
        "assigned_organization_id": str(world.contractor_org),
        "assigned_user_id": str(world.contractor),
        **changes,
    }


def grant_payload(world, **changes):
    return {
        "project_id": str(world.project),
        "location_id": str(world.building),
        "subject_organization_id": str(world.contractor_org),
        "subject_user_id": str(world.contractor),
        "permissions": ["cable:read"],
        **changes,
    }


def test_dashboard_and_search_filter_actual_resources_for_scoped_account(endpoint_world):
    world, client, ids = endpoint_world
    scoped = client.get("/api/v1/dashboard", headers=headers(world))
    owner = client.get("/api/v1/dashboard", headers=headers(world, world.admin))
    assert scoped.status_code == owner.status_code == 200
    assert scoped.json()["counts"]["buildings"] == 1
    assert scoped.json()["counts"]["racks"] == 1
    assert scoped.json()["counts"]["failed_tests"] == 1
    assert owner.json()["counts"]["racks"] == 2
    assert owner.json()["counts"]["failed_tests"] == 2
    assert str(ids["order"]) not in {row["id"] for row in scoped.json()["recent_work_orders"]}
    for query in ["OUTSIDE", "Other", "000"]:
        hidden = client.get("/api/v1/search", params={"q": query}, headers=headers(world))
        assert hidden.status_code == 200
        hidden_ids = {str(ids[name]) for name in ("rack", "device", "cable", "order")}
        assert not hidden_ids.intersection(row["id"] for row in hidden.json()["items"])
    limited = client.get("/api/v1/search", params={"q": "000", "limit": 1}, headers=headers(world))
    assert limited.status_code == 200 and len(limited.json()["items"]) == 1
    visible = client.get("/api/v1/search", params={"q": "TR01"}, headers=headers(world))
    assert visible.status_code == 200 and visible.json()["items"]
    assert client.get(
        "/api/v1/search", params={"q": "OUTSIDE"}, headers=headers(world, world.admin)
    ).json()["items"]


@pytest.mark.parametrize(
    "route", ["compliance/report", "audit-events", "reports/cable-schedule.csv"]
)
def test_full_tenant_reports_do_not_expose_partial_accounts_even_with_named_permission(
    endpoint_world, route
):
    world, client, _ = endpoint_world
    assert client.get(f"/api/v1/{route}", headers=headers(world)).status_code == 403
    assert client.get(f"/api/v1/{route}", headers=headers(world, world.admin)).status_code == 200


def test_template_catalog_only_exposes_devices_in_scoped_location(endpoint_world):
    world, client, ids = endpoint_world
    response = client.get("/api/v1/device-templates", headers=headers(world))
    assert response.status_code == 200
    template_ids = {row["id"] for row in response.json()}
    assert str(world.switch_template) in template_ids
    assert str(ids["template"]) not in template_ids
    owner = client.get("/api/v1/device-templates", headers=headers(world, world.admin))
    assert str(ids["template"]) in {row["id"] for row in owner.json()}
    payload = {
        "manufacturer": "A",
        "model": "B",
        "device_type": "switch",
        "rack_units": 1,
        "port_blueprint": [],
    }
    assert (
        client.post("/api/v1/device-templates", json=payload, headers=headers(world)).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/device-templates", json=payload, headers=headers(world, world.admin)
        ).status_code
        == 201
    )


def test_authorized_work_order_keeps_assignee_and_supports_cable_only_scope(endpoint_world):
    world, client, _ = endpoint_world
    first = client.post("/api/v1/work-orders", json=order_payload(world), headers=headers(world))
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/work-orders",
        json=order_payload(world, work_order_number="CABLE-ONLY", location_id=None),
        headers=headers(world, world.admin),
    )
    assert second.status_code == 201, second.text
    assert second.json()["assigned_user_id"] == str(world.contractor)


@pytest.mark.parametrize(
    "kind",
    [
        "foreign_project",
        "foreign_location",
        "other_project",
        "other_location",
        "bad_assignee",
        "mismatch_cable",
    ],
)
def test_work_orders_reject_foreign_and_unauthorized_actual_references_without_writes(
    endpoint_world, kind
):
    world, client, ids = endpoint_world
    changes = {
        "foreign_project": {"project_id": str(ids["foreign_project"])},
        "foreign_location": {"location_id": str(ids["foreign_location"])},
        "other_project": {"project_id": str(ids["project"]), "cable_id": None},
        "other_location": {"location_id": str(world.other_building)},
        "bad_assignee": {"assigned_user_id": str(world.admin)},
        "mismatch_cable": {"cable_id": str(ids["cable"])},
    }[kind]
    with world.scoped_session() as db:
        before = db.scalar(select(func.count(WorkOrder.id)))
    result = client.post(
        "/api/v1/work-orders", json=order_payload(world, **changes), headers=headers(world)
    )
    assert result.status_code in {403, 404, 422}, result.text
    with world.scoped_session() as db:
        assert db.scalar(select(func.count(WorkOrder.id))) == before


def test_member_cannot_create_cross_tenant_work_order_references(endpoint_world):
    world, client, ids = endpoint_world
    result = client.post(
        "/api/v1/work-orders",
        json=order_payload(world, location_id=str(ids["foreign_location"])),
        headers=headers(world, world.admin),
    )
    assert result.status_code == 404


def test_scoped_accounts_cannot_delegate_or_revoke_even_when_grant_contains_permission(
    endpoint_world,
):
    world, client, ids = endpoint_world
    assert (
        client.post(
            "/api/v1/access-grants", json=grant_payload(world), headers=headers(world)
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/access-grants/{ids['grant']}/revoke", headers=headers(world)
        ).status_code
        == 403
    )


def test_member_grants_valid_account_then_revokes_and_rejects_foreign_grant(endpoint_world):
    world, client, ids = endpoint_world
    created = client.post(
        "/api/v1/access-grants", json=grant_payload(world), headers=headers(world, world.admin)
    )
    assert created.status_code == 201, created.text
    assert (
        client.post(
            f"/api/v1/access-grants/{created.json()['id']}/revoke",
            headers=headers(world, world.admin),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/access-grants/{ids['foreign_grant']}/revoke",
            headers=headers(world, world.admin),
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "kind",
    [
        "foreign_project",
        "foreign_location",
        "missing_subject",
        "wrong_organization",
        "empty_permissions",
        "bad_expiry",
    ],
)
def test_grants_validate_tenant_references_subject_and_lifetime(endpoint_world, kind):
    world, client, ids = endpoint_world
    changes = {
        "foreign_project": {"project_id": str(ids["foreign_project"])},
        "foreign_location": {"location_id": str(ids["foreign_location"])},
        "missing_subject": {"subject_user_id": None},
        "wrong_organization": {"subject_user_id": str(world.admin)},
        "empty_permissions": {"permissions": []},
        "bad_expiry": {"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
    }[kind]
    result = client.post(
        "/api/v1/access-grants",
        json=grant_payload(world, **changes),
        headers=headers(world, world.admin),
    )
    assert result.status_code in {404, 422}, result.text


def test_limited_member_cannot_delegate_more_permissions_and_personal_space_stays_private(
    endpoint_world,
):
    world, client, _ = endpoint_world
    with world.scoped_session() as db:
        member = db.scalar(
            select(TenantMembership).where(TenantMembership.user_id == world.supervisor)
        )
        member.permissions = member.permissions + ["access_grant:create"]
        db.commit()
    assert (
        client.post(
            "/api/v1/access-grants",
            json=grant_payload(world, permissions=["*"]),
            headers=headers(world, world.supervisor),
        ).status_code
        == 403
    )
    with world.scoped_session() as db:
        tenant = db.get(Tenant, world.tenant_a)
        tenant.workspace_kind = "personal"
        tenant.workspace_owner_id = world.admin
        db.commit()
    assert (
        client.post(
            "/api/v1/access-grants", json=grant_payload(world), headers=headers(world, world.admin)
        ).status_code
        == 403
    )
    assert client.get("/api/v1/dashboard", headers=headers(world)).status_code == 403


def test_stale_service_principal_is_rechecked_after_grant_revocation(endpoint_world):
    world, _client, ids = endpoint_world
    with world.scoped_session() as db:
        stale = resolve_principal(
            db,
            actor_id=world.contractor,
            tenant_id=world.tenant_a,
            project_id=world.project,
            location_id=world.building,
        )
        grant = db.get(AccessGrant, ids["grant"])
        grant.status = AccessGrantStatus.REVOKED
        db.commit()
        with pytest.raises(AuthorizationError):
            ReportingService(db, stale).search("TR01")
        with pytest.raises(AuthorizationError):
            ComplianceService(db, stale).report()

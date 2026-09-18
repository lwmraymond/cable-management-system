from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import deps
from app.config import Settings
from app.exceptions import AuthorizationError
from app.main import app
from app.models import (
    AccessGrant,
    AccessGrantStatus,
    AuditEvent,
    Tenant,
    TenantMembership,
    UserIdentity,
)
from app.security import resolve_principal
from app.services.workspaces import WorkspaceService


@pytest.fixture
def client(world, monkeypatch):
    # Each test is an independent server; do not share its IP quota with earlier tests.
    monkeypatch.setattr(app, "middleware_stack", None)
    settings = Settings(_env_file=None, auth_mode="demo", demo_mode=True, rate_limit_enabled=False)
    monkeypatch.setattr(deps, "SessionLocal", world.session_factory)
    monkeypatch.setattr(deps, "PlatformSessionLocal", world.session_factory)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    return TestClient(app, base_url="https://testserver")


def headers(actor, tenant=None):
    result = {"X-Actor-ID": str(actor)}
    if tenant:
        result["X-Tenant-ID"] = str(tenant)
    return result


def create(client, world, kind="personal"):
    response = client.post(
        "/api/v1/workspaces",
        headers=headers(world.admin),
        json={"name": "  My Workspace  ", "kind": kind},
    )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "My Workspace"
    return response.json()


def test_discovery_only_returns_account_authorized_workspaces(client, world):
    response = client.get("/api/v1/auth/session", headers=headers(world.admin))
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == str(world.admin)
    assert body["auth_method"] == "demo"
    assert body["can_create_workspaces"] is True
    assert [item["id"] for item in body["workspaces"]] == [str(world.tenant_a)]
    assert body["workspaces"][0]["kind"] == "shared"
    assert body["workspaces"][0]["can_manage"] is True
    assert response.headers["cache-control"] == "no-store"


def test_personal_workspace_creates_separate_empty_dataset_and_audits(client, world):
    workspace = create(client, world)
    assert workspace["kind"] == "personal"
    assert workspace["permissions"] == ["*"]
    assert client.get("/api/v1/racks", headers=headers(world.admin, workspace["id"])).json() == []
    with world.session_factory() as db:
        tenant = db.get(Tenant, uuid.UUID(workspace["id"]))
        assert tenant.workspace_owner_id == world.admin
        assert tenant.active_standard_profile_id is not None
        events = db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant.id)).all()
        assert [event.action for event in events] == ["workspace.create"]


def test_personal_privacy_overrides_accidental_membership(client, world):
    workspace = create(client, world)
    tenant_id = uuid.UUID(workspace["id"])
    with world.session_factory() as db:
        db.add(
            TenantMembership(
                tenant_id=tenant_id,
                user_id=world.supervisor,
                role="owner",
                permissions=["*"],
                active=True,
            )
        )
        db.commit()
    listing = client.get("/api/v1/workspaces", headers=headers(world.supervisor)).json()
    assert workspace["id"] not in [item["id"] for item in listing]
    assert (
        client.get("/api/v1/racks", headers=headers(world.supervisor, tenant_id)).status_code == 403
    )
    assert (
        client.get(
            f"/api/v1/workspaces/{tenant_id}/members", headers=headers(world.supervisor)
        ).status_code
        == 404
    )


def test_sharing_requires_explicit_conversion_and_supports_read_only_revocation(client, world):
    workspace = create(client, world)
    endpoint = f"/api/v1/workspaces/{workspace['id']}"
    member = {"email": "SUPERVISOR@test.example", "role": "viewer"}
    assert (
        client.put(endpoint + "/members", json=member, headers=headers(world.admin)).status_code
        == 409
    )
    response = client.patch(endpoint, json={"kind": "shared"}, headers=headers(world.admin))
    assert response.status_code == 200
    assert response.json()["kind"] == "shared"
    response = client.put(endpoint + "/members", json=member, headers=headers(world.admin))
    assert response.status_code == 200
    shared = client.get("/api/v1/workspaces", headers=headers(world.supervisor)).json()
    item = next(item for item in shared if item["id"] == workspace["id"])
    assert item["role"] == "viewer" and not item["can_manage"]
    denied_write = client.post(
        "/api/v1/locations",
        headers=headers(world.supervisor, workspace["id"]),
        json={"location_type": "room", "identifier": "VIEWER-ROOM", "name": "Denied room"},
    )
    assert denied_write.status_code == 403
    with world.session_factory() as db:
        principal = resolve_principal(
            db, actor_id=world.supervisor, tenant_id=uuid.UUID(workspace["id"])
        )
        assert principal.can("rack:read") and not principal.can("rack:create")
    assert client.get(endpoint + "/members", headers=headers(world.supervisor)).status_code == 403
    assert (
        client.patch(endpoint, json={"kind": "personal"}, headers=headers(world.admin)).status_code
        == 409
    )
    assert (
        client.delete(
            endpoint + f"/members/{world.supervisor}", headers=headers(world.admin)
        ).status_code
        == 204
    )
    assert (
        client.get("/api/v1/racks", headers=headers(world.supervisor, workspace["id"])).status_code
        == 403
    )
    assert (
        client.patch(endpoint, json={"kind": "personal"}, headers=headers(world.admin)).status_code
        == 200
    )
    with world.session_factory() as db:
        actions = list(
            db.scalars(
                select(AuditEvent.action).where(AuditEvent.tenant_id == uuid.UUID(workspace["id"]))
            )
        )
        assert actions == [
            "workspace.create",
            "workspace.update",
            "workspace.member.upsert",
            "workspace.member.remove",
            "workspace.update",
        ]


def test_editor_can_edit_inventory_but_cannot_manage_or_approve(client, world):
    workspace = create(client, world, "shared")
    endpoint = f"/api/v1/workspaces/{workspace['id']}"
    response = client.put(
        endpoint + "/members",
        headers=headers(world.admin),
        json={"email": "supervisor@test.example", "role": "editor"},
    )
    assert response.status_code == 200
    created_location = client.post(
        "/api/v1/locations",
        headers=headers(world.supervisor, workspace["id"]),
        json={"location_type": "room", "identifier": "EDITOR-ROOM", "name": "Editor room"},
    )
    assert created_location.status_code == 201, created_location.text
    assert created_location.json()["tenant_id"] == workspace["id"]
    with world.session_factory() as db:
        principal = resolve_principal(
            db, actor_id=world.supervisor, tenant_id=uuid.UUID(workspace["id"])
        )
        assert principal.can("location:create") and principal.can("floor_plan:write")
        assert not principal.can("*") and not principal.can("access_grant:create")
        assert not principal.can("cable:approve")
    assert (
        client.patch(
            endpoint, json={"name": "Hacked"}, headers=headers(world.supervisor)
        ).status_code
        == 403
    )
    assert (
        client.put(
            endpoint + "/members",
            headers=headers(world.supervisor),
            json={"email": "contractor@test.example", "role": "editor"},
        ).status_code
        == 403
    )


@pytest.mark.parametrize("role", ["owner", "admin", "*"])
def test_member_roles_cannot_escalate(client, world, role):
    workspace = create(client, world, "shared")
    response = client.put(
        f"/api/v1/workspaces/{workspace['id']}/members",
        headers=headers(world.admin),
        json={"email": "supervisor@test.example", "role": role},
    )
    assert response.status_code == 422


def test_owner_and_existing_admin_are_protected(client, world):
    for endpoint in [
        f"/api/v1/workspaces/{world.tenant_a}",
        f"/api/v1/workspaces/{create(client, world, 'shared')['id']}",
    ]:
        assert (
            client.put(
                endpoint + "/members",
                json={"email": "admin@test.example", "role": "viewer"},
                headers=headers(world.admin),
            ).status_code
            == 409
        )
        assert (
            client.delete(
                endpoint + f"/members/{world.admin}", headers=headers(world.admin)
            ).status_code
            == 409
        )


def test_invites_require_active_provisioned_accounts(client, world):
    workspace = create(client, world, "shared")
    endpoint = f"/api/v1/workspaces/{workspace['id']}/members"
    assert (
        client.put(
            endpoint,
            headers=headers(world.admin),
            json={"email": "unknown@test.example", "role": "viewer"},
        ).status_code
        == 404
    )
    with world.session_factory() as db:
        db.get(UserIdentity, world.supervisor).active = False
        db.commit()
    assert (
        client.put(
            endpoint,
            headers=headers(world.admin),
            json={"email": "supervisor@test.example", "role": "viewer"},
        ).status_code
        == 404
    )


def test_contractor_discovery_keeps_live_scopes_and_cannot_gain_wide_membership(client, world):
    with world.session_factory() as db:
        first = db.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        for location, expiry in [
            (world.tr, datetime.now(UTC) + timedelta(days=1)),
            (world.other_building, datetime.now(UTC) - timedelta(days=1)),
        ]:
            db.add(
                AccessGrant(
                    tenant_id=world.tenant_a,
                    subject_user_id=world.contractor,
                    subject_organization_id=world.contractor_org,
                    project_id=world.project,
                    location_id=location,
                    approved_by=world.admin,
                    permissions=["cable:approve"],
                    starts_at=first.starts_at,
                    expires_at=expiry,
                    status=AccessGrantStatus.ACTIVE,
                )
            )
        db.commit()
    workspaces = client.get("/api/v1/workspaces", headers=headers(world.contractor)).json()
    assert len(workspaces) == 1
    item = workspaces[0]
    assert item["project_id"] == str(world.project) and item["location_id"] == str(world.building)
    assert item["scopes"] == [
        {"project_id": str(world.project), "location_id": str(world.building)},
        {"project_id": str(world.project), "location_id": str(world.tr)},
    ]
    assert "cable:approve" not in item["permissions"]
    assert item["can_manage"] is False
    endpoint = f"/api/v1/workspaces/{world.tenant_a}"
    assert (
        client.put(
            endpoint + "/members",
            headers=headers(world.admin),
            json={"email": "contractor@test.example", "role": "editor"},
        ).status_code
        == 409
    )
    assert client.get(endpoint + "/members", headers=headers(world.contractor)).status_code == 403


def test_expired_revoked_future_grants_and_inactive_workspaces_are_hidden(client, world):
    with world.session_factory() as db:
        grant = db.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        grant.starts_at = datetime.now(UTC) + timedelta(days=1)
        db.commit()
    assert client.get("/api/v1/workspaces", headers=headers(world.contractor)).json() == []
    with world.session_factory() as db:
        db.get(Tenant, world.tenant_a).active = False
        db.commit()
    assert client.get("/api/v1/workspaces", headers=headers(world.admin)).json() == []
    assert (
        client.get("/api/v1/racks", headers=headers(world.admin, world.tenant_a)).status_code == 403
    )


@pytest.mark.parametrize("actor", [None, "unknown", "inactive"])
def test_account_discovery_rejects_invalid_identities(client, world, actor):
    if actor == "inactive":
        with world.session_factory() as db:
            db.get(UserIdentity, world.admin).active = False
            db.commit()
        actor_id = world.admin
    else:
        actor_id = uuid.uuid4()
    response = client.get("/api/v1/auth/session", headers=headers(actor_id) if actor else {})
    assert response.status_code == 401


def test_bound_claim_applies_to_discovery_create_update_and_member_paths(world):
    settings = Settings(_env_file=None)
    with world.session_factory() as db:
        service = WorkspaceService(
            db,
            db.get(UserIdentity, world.admin),
            {settings.oidc_tenant_claim: str(world.tenant_b)},
            settings,
        )
        assert service.list_workspaces() == []
        assert service.can_create is False
        with pytest.raises(AuthorizationError, match="tenant-bound"):
            service.create("No bypass", "personal")
        for action in [
            lambda: service.update(world.tenant_a, name="No bypass", kind=None),
            lambda: service.members(world.tenant_a),
            lambda: service.put_member(
                world.tenant_a, email="supervisor@test.example", role="editor"
            ),
            lambda: service.remove_member(world.tenant_a, world.supervisor),
        ]:
            with pytest.raises(AuthorizationError, match="tenant claim"):
                action()
        with pytest.raises(AuthorizationError, match="invalid"):
            WorkspaceService(
                db, db.get(UserIdentity, world.admin), {settings.oidc_tenant_claim: []}, settings
            )


@pytest.mark.parametrize(
    "body",
    [
        {"name": " "},
        {"name": "x" * 181},
        {"name": "valid", "kind": "public"},
        {"name": "valid", "owner_id": "attacker"},
    ],
)
def test_creation_validation_rejects_invalid_or_mass_assigned_fields(client, world, body):
    assert (
        client.post("/api/v1/workspaces", headers=headers(world.admin), json=body).status_code
        == 422
    )


@pytest.mark.parametrize("state", ["expired", "revoked", "deleted"])
def test_unusable_grants_are_not_discoverable(client, world, state):
    with world.session_factory() as db:
        grant = db.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        if state == "expired":
            grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif state == "revoked":
            grant.status = AccessGrantStatus.REVOKED
        else:
            grant.deleted_at = datetime.now(UTC)
        db.commit()
    assert client.get("/api/v1/workspaces", headers=headers(world.contractor)).json() == []
    request_headers = headers(world.contractor, world.tenant_a) | {
        "X-Project-ID": str(world.project),
        "X-Location-ID": str(world.building),
    }
    assert client.get("/api/v1/racks", headers=request_headers).status_code == 403


def test_verified_cookie_session_issues_csrf_and_preserves_bound_claim(client, world, monkeypatch):
    from test_browser_auth import encoded_token, settings_for

    settings = settings_for()
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    with world.session_factory() as db:
        db.get(UserIdentity, world.admin).oidc_subject = "browser-admin"
        db.commit()
    client.cookies.set(settings.auth_cookie_name, encoded_token(tenant_id=str(world.tenant_a)))
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json()["auth_method"] == "cookie"
    assert response.json()["can_create_workspaces"] is False
    assert [item["id"] for item in response.json()["workspaces"]] == [str(world.tenant_a)]
    csrf = client.cookies.get(settings.csrf_cookie_name)
    assert csrf and len(csrf) >= 32
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" not in response.headers["set-cookie"]
    assert "set-cookie" not in client.get("/api/v1/auth/session").headers
    denied = client.post(
        "/api/v1/workspaces",
        json={"name": "Outside credential scope"},
        headers={settings.csrf_header_name: csrf},
    )
    assert denied.status_code == 403


def test_bad_cookie_never_bootstraps_csrf_or_uses_demo_identity(client, world, monkeypatch):
    from test_browser_auth import settings_for

    settings = settings_for(auth_mode="hybrid", demo_mode=True)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    client.cookies.set(settings.auth_cookie_name, "invalid-token")
    response = client.get("/api/v1/auth/session", headers=headers(world.admin))
    assert response.status_code == 401
    assert settings.csrf_cookie_name not in client.cookies


def test_workspace_capabilities_distinguish_owner_and_member(client, world):
    workspace = create(client, world, "shared")
    endpoint = f"/api/v1/workspaces/{workspace['id']}"
    assert workspace["is_owner"] is True and workspace["can_leave"] is False
    assert workspace["member_count"] == 1
    response = client.put(
        endpoint + "/members",
        headers=headers(world.admin),
        json={"email": "supervisor@test.example", "role": "editor"},
    )
    assert response.status_code == 200
    member = next(row for row in response.json() if row["user_id"] == str(world.supervisor))
    assert member["version"] >= 1 and member["has_scoped_access"] is False
    shared = client.get("/api/v1/workspaces", headers=headers(world.supervisor)).json()
    item = next(row for row in shared if row["id"] == workspace["id"])
    assert item["can_leave"] is True and item["is_owner"] is False and item["member_count"] == 2


def test_member_version_blocks_stale_role_change_and_revoke(client, world):
    workspace = create(client, world, "shared")
    endpoint = f"/api/v1/workspaces/{workspace['id']}/members"
    response = client.put(
        endpoint,
        headers=headers(world.admin),
        json={"email": "supervisor@test.example", "role": "viewer"},
    )
    version = next(
        row["version"] for row in response.json() if row["user_id"] == str(world.supervisor)
    )
    changed = client.put(
        endpoint,
        headers=headers(world.admin),
        json={"email": "supervisor@test.example", "role": "editor", "expected_version": version},
    )
    assert changed.status_code == 200
    latest = next(
        row["version"] for row in changed.json() if row["user_id"] == str(world.supervisor)
    )
    assert latest > version
    assert (
        client.put(
            endpoint,
            headers=headers(world.admin),
            json={
                "email": "supervisor@test.example",
                "role": "viewer",
                "expected_version": version,
            },
        ).status_code
        == 409
    )
    assert (
        client.delete(
            f"{endpoint}/{world.supervisor}?expected_version={version}&revoke_scoped_access=true",
            headers=headers(world.admin),
        ).status_code
        == 409
    )
    rows = client.get(endpoint, headers=headers(world.admin)).json()
    assert next(row["role"] for row in rows if row["user_id"] == str(world.supervisor)) == "editor"
    assert (
        client.delete(
            f"{endpoint}/{world.supervisor}?expected_version={latest}&revoke_scoped_access=true",
            headers=headers(world.admin),
        ).status_code
        == 204
    )


def test_member_leaves_without_deleting_shared_content_then_owner_makes_private(client, world):
    from app.models import Location

    workspace = create(client, world, "shared")
    endpoint = f"/api/v1/workspaces/{workspace['id']}"
    assert (
        client.put(
            endpoint + "/members",
            headers=headers(world.admin),
            json={"email": "supervisor@test.example", "role": "editor"},
        ).status_code
        == 200
    )
    room = client.post(
        "/api/v1/locations",
        headers=headers(world.supervisor, workspace["id"]),
        json={"location_type": "room", "identifier": "SHARED-ROOM", "name": "Shared room"},
    )
    assert room.status_code == 201, room.text
    assert client.post(endpoint + "/leave", headers=headers(world.supervisor)).status_code == 204
    assert workspace["id"] not in {
        row["id"]
        for row in client.get("/api/v1/workspaces", headers=headers(world.supervisor)).json()
    }
    assert (
        client.get(
            "/api/v1/locations", headers=headers(world.supervisor, workspace["id"])
        ).status_code
        == 403
    )
    private = client.patch(endpoint, headers=headers(world.admin), json={"kind": "personal"})
    assert private.status_code == 200 and private.json()["kind"] == "personal"
    with world.session_factory() as db:
        assert db.get(Location, uuid.UUID(room.json()["id"])) is not None
        assert (
            db.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.tenant_id == uuid.UUID(workspace["id"]),
                    AuditEvent.action == "workspace.leave",
                )
            )
            is not None
        )


def test_scoped_collaborator_can_leave_and_revoke_all_own_access(client, world):
    response = client.post(
        f"/api/v1/workspaces/{world.tenant_a}/leave", headers=headers(world.contractor)
    )
    assert response.status_code == 204, response.text
    assert client.get("/api/v1/workspaces", headers=headers(world.contractor)).json() == []
    with world.session_factory() as db:
        grants = db.scalars(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        ).all()
        assert grants and all(grant.status == AccessGrantStatus.REVOKED for grant in grants)
        assert all(grant.revoked_by == world.contractor for grant in grants)


def test_revoke_can_explicitly_remove_member_and_independent_grants(client, world):
    with world.session_factory() as db:
        db.add(
            TenantMembership(
                tenant_id=world.tenant_a,
                user_id=world.contractor,
                role="viewer",
                permissions=["location:read"],
                active=True,
            )
        )
        db.commit()
    endpoint = f"/api/v1/workspaces/{world.tenant_a}/members"
    rows = client.get(endpoint, headers=headers(world.admin)).json()
    member = next(row for row in rows if row["user_id"] == str(world.contractor))
    assert member["has_scoped_access"] is True
    response = client.delete(
        f"{endpoint}/{world.contractor}?revoke_scoped_access=true&expected_version={member['version']}",
        headers=headers(world.admin),
    )
    assert response.status_code == 204
    assert client.get("/api/v1/workspaces", headers=headers(world.contractor)).json() == []
    with world.session_factory() as db:
        assert (
            db.scalar(
                select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
            ).status
            == AccessGrantStatus.REVOKED
        )


def test_owner_cannot_leave_and_foreign_account_cannot_withdraw_another_owner(client, world):
    workspace = create(client, world)
    endpoint = f"/api/v1/workspaces/{workspace['id']}/leave"
    assert client.post(endpoint, headers=headers(world.admin)).status_code == 409
    assert client.post(endpoint, headers=headers(world.supervisor)).status_code == 404
    assert (
        client.post(
            f"/api/v1/workspaces/{world.tenant_a}/leave", headers=headers(world.admin)
        ).status_code
        == 409
    )


def test_personal_workspace_rejects_foreign_project_or_location_headers(client, world):
    workspace = create(client, world)
    for key, value in [("X-Project-ID", world.project), ("X-Location-ID", world.tr)]:
        response = client.get(
            "/api/v1/racks", headers=headers(world.admin, workspace["id"]) | {key: str(value)}
        )
        assert response.status_code == 403
        assert (
            response.json()["detail"] == "Requested project or location is outside this workspace"
        )


def test_long_lived_identity_cache_does_not_preserve_downgraded_permissions(client, world):
    with world.session_factory() as cached:
        initial = resolve_principal(cached, actor_id=world.admin, tenant_id=world.tenant_a)
        assert initial.can("rack:create")
        # Keep the identity-map row alive, as a long-lived consumer can do.
        membership = cached.scalar(
            select(TenantMembership).where(
                TenantMembership.user_id == world.admin,
                TenantMembership.tenant_id == world.tenant_a,
            )
        )
        with world.session_factory() as change:
            member = change.get(TenantMembership, membership.id)
            member.permissions = ["rack:read"]
            member.role = "viewer"
            change.commit()
        fresh = resolve_principal(cached, actor_id=world.admin, tenant_id=world.tenant_a)
        assert not fresh.can("rack:create") and fresh.can("rack:read")


@pytest.fixture
def multi_scope_revision(world):
    """A second scope keeps existing access after one of its two grants changes."""
    with world.session_factory() as db:
        first = db.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        scoped_read = AccessGrant(
            tenant_id=world.tenant_a,
            subject_user_id=world.contractor,
            subject_organization_id=world.contractor_org,
            project_id=world.project,
            location_id=world.other_building,
            approved_by=world.admin,
            permissions=["cable:read", "cable:trace"],
            starts_at=first.starts_at,
            expires_at=first.expires_at,
            status=AccessGrantStatus.ACTIVE,
        )
        scoped_write = AccessGrant(
            tenant_id=world.tenant_a,
            subject_user_id=world.contractor,
            subject_organization_id=world.contractor_org,
            project_id=world.project,
            location_id=world.other_building,
            approved_by=world.admin,
            permissions=["rack:create"],
            starts_at=first.starts_at,
            expires_at=first.expires_at,
            status=AccessGrantStatus.ACTIVE,
        )
        db.add_all([scoped_read, scoped_write])
        db.commit()
        return scoped_read.id


@pytest.mark.parametrize("change", ["permissions", "revoke", "expire", "extend", "start"])
def test_session_revision_covers_changes_outside_first_scope(
    client, world, multi_scope_revision, change
):
    endpoint = "/api/v1/auth/session"
    before = client.get(endpoint, headers=headers(world.contractor)).json()["workspaces"][0]
    with world.session_factory() as db:
        grant = db.get(AccessGrant, multi_scope_revision)
        if change == "permissions":
            grant.permissions = ["port:read"]
        elif change == "revoke":
            grant.status = AccessGrantStatus.REVOKED
        elif change == "expire":
            grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "extend":
            grant.expires_at += timedelta(days=1)
        else:
            grant.starts_at -= timedelta(hours=1)
        db.commit()
        actor = resolve_principal(
            db,
            actor_id=world.contractor,
            tenant_id=world.tenant_a,
            project_id=world.project,
            location_id=world.other_building,
        )
        assert actor.can("rack:create")
        assert actor.can("cable:read") is (change in {"extend", "start"})
    after = client.get(endpoint, headers=headers(world.contractor)).json()["workspaces"][0]
    assert before["access_revision"] != after["access_revision"]
    # Existing scope and permissions fields cannot detect this transition.
    assert {key: value for key, value in before.items() if key != "access_revision"} == {
        key: value for key, value in after.items() if key != "access_revision"
    }


def test_access_revision_is_stable_for_equivalent_grant_and_permission_order(
    world, multi_scope_revision, monkeypatch
):
    with world.session_factory() as db:
        service = WorkspaceService(
            db,
            db.get(UserIdentity, world.contractor),
            None,
            Settings(_env_file=None, auth_mode="demo", demo_mode=True),
        )
        tenant = db.get(Tenant, world.tenant_a)
        before = service.describe(tenant)["access_revision"]
        grants = service._grants(world.tenant_a)
        for grant in grants:
            grant.permissions = list(reversed(grant.permissions)) + grant.permissions[:1]
        db.flush()
        monkeypatch.setattr(service, "_grants", lambda _workspace_id: list(reversed(grants)))
        assert service.describe(tenant)["access_revision"] == before
        assert len(before) == 64 and all(character in "0123456789abcdef" for character in before)


def test_member_revision_tracks_permissions_without_changing_for_display_name(client, world):
    before = client.get("/api/v1/workspaces", headers=headers(world.supervisor)).json()[0]
    with world.session_factory() as db:
        membership = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == world.tenant_a,
                TenantMembership.user_id == world.supervisor,
            )
        )
        membership.permissions = list(reversed(membership.permissions))
        db.get(Tenant, world.tenant_a).name = "Renamed workspace"
        db.commit()
    renamed = client.get("/api/v1/workspaces", headers=headers(world.supervisor)).json()[0]
    assert renamed["access_revision"] == before["access_revision"]
    with world.session_factory() as db:
        membership = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == world.tenant_a,
                TenantMembership.user_id == world.supervisor,
            )
        )
        membership.permissions = ["rack:read"]
        db.commit()
    changed = client.get("/api/v1/workspaces", headers=headers(world.supervisor)).json()[0]
    assert changed["access_revision"] != before["access_revision"]

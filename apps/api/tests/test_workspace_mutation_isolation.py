"""Workspace mutations must validate stored references and actual account authority."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import deps
from app.config import Settings
from app.exceptions import AuthorizationError, NotFoundError
from app.main import app
from app.models import (
    AccessGrant,
    AccessGrantStatus,
    Cable,
    Device,
    DeviceTemplate,
    Location,
    LocationType,
    Pathway,
    PathwaySegment,
    Port,
    Project,
    Rack,
    Tenant,
    TenantMembership,
    UserIdentity,
)
from app.schemas_scene_editor import CablePolicyUpdate, RoutePreview
from app.security import resolve_principal
from app.services.connectivity import ConnectivityService
from app.services.infrastructure import InfrastructureService
from app.services.labels import LabelService
from app.services.scene import SceneService
from app.services.scene_editor import SceneEditorService
from app.services.scene_routing import SceneRouteService
from app.services.workspaces import EDITOR_PERMISSIONS, VIEWER_PERMISSIONS


@pytest.fixture
def mutation_world(world, monkeypatch):
    monkeypatch.setattr(app, "middleware_stack", None)
    monkeypatch.setattr(deps, "SessionLocal", world.session_factory)
    monkeypatch.setattr(deps, "PlatformSessionLocal", world.session_factory)
    settings = Settings(_env_file=None, auth_mode="demo", demo_mode=True, rate_limit_enabled=False)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    with world.session_factory() as db:
        db.info["bypass_tenant"] = True
        grant = db.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        grant.permissions = list(EDITOR_PERMISSIONS)
        ids = {}
        for prefix, tenant_id, location_id in [
            ("outside", world.tenant_a, world.other_building),
            ("foreign", world.tenant_b, db.get(Rack, world.private_rack).location_id),
        ]:
            tenant = db.get(Tenant, tenant_id)
            project = Project(
                tenant_id=tenant_id,
                project_number=prefix,
                name=prefix,
                customer_organization_id=tenant.owner_organization_id,
            )
            rack = Rack(
                tenant_id=tenant_id,
                location_id=location_id,
                rack_identifier=prefix + "-RACK",
                name=prefix,
            )
            template = DeviceTemplate(
                tenant_id=tenant_id,
                manufacturer=prefix,
                model=prefix,
                device_type="switch",
                rack_units=1,
                port_blueprint=[],
            )
            pathway = Pathway(
                tenant_id=tenant_id,
                location_id=location_id,
                identifier=prefix + "-TRAY",
                name=prefix,
                pathway_type="tray",
            )
            db.add_all([project, rack, template, pathway])
            db.flush()
            device = Device(
                tenant_id=tenant_id,
                location_id=location_id,
                rack_id=rack.id,
                identifier=prefix + "-DEVICE",
                name=prefix,
                device_type="switch",
            )
            cable = Cable(
                tenant_id=tenant_id,
                project_id=project.id,
                identifier=prefix + "-CABLE",
                media_type="copper",
                construction="patch_cord",
            )
            segment = PathwaySegment(
                tenant_id=tenant_id, pathway_id=pathway.id, sequence=1, name=prefix, length_m=1
            )
            db.add_all([device, cable, segment])
            db.flush()
            port = Port(
                tenant_id=tenant_id,
                device_id=device.id,
                identifier="P1",
                label="P1",
                connector_type="RJ45",
                media_type="copper",
                position_index=1,
            )
            db.add(port)
            db.flush()
            ids[prefix] = {
                "location": location_id,
                "project": project.id,
                "rack": rack.id,
                "template": template.id,
                "device": device.id,
                "cable": cable.id,
                "segment": segment.id,
                "port": port.id,
            }
        ids["port_a"] = db.scalar(
            select(Port.id).where(Port.device_id == world.switch, Port.identifier == "G02")
        )
        ids["port_b"] = db.scalar(
            select(Port.id).where(Port.device_id == world.panel, Port.identifier == "F02")
        )
        db.commit()
    return world, ids, TestClient(app)


def principal(db, world, actor=None):
    return resolve_principal(
        db,
        actor_id=actor or world.contractor,
        tenant_id=world.tenant_a,
        project_id=world.project,
        location_id=world.building,
    )


def operation(db, actor, world, ids, name, target=None):
    target = target or ids["outside"]
    infra = InfrastructureService(db, actor)
    cable = ConnectivityService(db, actor)
    cable_args = dict(
        identifier="NEW-CABLE",
        media_type="copper",
        construction="patch_cord",
        port_a_id=ids["port_a"],
        port_b_id=ids["port_b"],
        project_id=world.project,
    )
    if name == "parent":
        return infra.create_location(
            location_type=LocationType.ROOM,
            identifier="NEW-ROOM",
            name="Room",
            parent_id=target["location"],
        )
    if name == "root":
        return infra.create_location(
            location_type=LocationType.CAMPUS, identifier="NEW-ROOT", name="Root"
        )
    if name == "rack":
        return infra.create_rack(
            location_id=target["location"], rack_identifier="NEW-RACK", name="Rack"
        )
    if name == "device-rack":
        return infra.create_device_from_template(
            rack_id=target["rack"],
            template_id=world.switch_template,
            identifier="NEW-DEVICE",
            name="Device",
            start_u=1,
        )
    if name == "device-template":
        return infra.create_device_from_template(
            rack_id=world.rack,
            template_id=target["template"],
            identifier="NEW-DEVICE",
            name="Device",
            start_u=1,
        )
    if name == "template":
        return infra.create_template(
            manufacturer="New", model="New", device_type="switch", rack_units=1, port_blueprint=[]
        )
    if name == "pathway":
        return infra.create_pathway(
            location_id=target["location"], identifier="NEW-TRAY", name="Tray", pathway_type="tray"
        )
    if name == "cable-project":
        cable_args["project_id"] = target["project"]
    elif name == "cable-port":
        cable_args["port_b_id"] = target["port"]
    elif name == "cable-route":
        cable_args["route_segment_ids"] = [target["segment"]]
    elif name == "cable-unassigned":
        cable_args["project_id"] = None
    elif name == "elevation":
        return infra.rack_elevation(target["rack"])
    elif name == "label":
        return LabelService(db, actor).create_cable_label(target["cable"], "https://sim.example")
    elif name == "trace":
        return cable.trace_cable(target["cable"])
    return cable.create_cable(**cable_args)


@pytest.mark.parametrize(
    "name",
    [
        "parent",
        "rack",
        "device-rack",
        "device-template",
        "pathway",
        "cable-project",
        "cable-port",
        "cable-route",
        "elevation",
        "label",
        "trace",
    ],
)
def test_cached_foreign_references_cannot_cross_workspace_even_in_bypass_session(
    mutation_world, name
):
    world, ids, _ = mutation_world
    with world.session_factory() as db:
        db.info["bypass_tenant"] = True
        # Populate SQLAlchemy's identity map deliberately: session.get alone is insufficient.
        for model, key in [
            (Location, "location"),
            (Rack, "rack"),
            (DeviceTemplate, "template"),
            (Port, "port"),
            (Project, "project"),
            (Cable, "cable"),
        ]:
            db.get(model, ids["foreign"][key])
        owner = principal(db, world, world.admin)
        with pytest.raises(NotFoundError):
            operation(db, owner, world, ids, name, ids["foreign"])
        assert not db.new


@pytest.mark.parametrize(
    "name",
    [
        "parent",
        "root",
        "rack",
        "device-rack",
        "template",
        "pathway",
        "cable-project",
        "cable-port",
        "cable-route",
        "cable-unassigned",
        "elevation",
        "label",
        "trace",
    ],
)
def test_valid_header_scope_cannot_authorize_unrelated_objects(mutation_world, name):
    world, ids, _ = mutation_world
    with world.scoped_session() as db:
        actor = principal(db, world)
        with pytest.raises(AuthorizationError):
            operation(db, actor, world, ids, name)
        assert not db.new


def test_scoped_editor_can_create_inside_grant_and_label_using_global_standard(mutation_world):
    world, ids, _ = mutation_world
    with world.scoped_session() as db:
        actor = principal(db, world)
        infra = InfrastructureService(db, actor)
        room = infra.create_location(
            location_type=LocationType.ROOM,
            parent_id=world.building,
            identifier="SCOPED-ROOM",
            name="Scoped room",
        )
        rack = infra.create_rack(location_id=room.id, rack_identifier="SCOPED-RACK", name="Rack")
        device = infra.create_device_from_template(
            rack_id=rack.id,
            template_id=world.switch_template,
            identifier="SCOPED-DEVICE",
            name="Device",
            start_u=1,
        )
        pathway = infra.create_pathway(
            location_id=room.id,
            identifier="SCOPED-TRAY",
            name="Tray",
            pathway_type="tray",
            segments=[{"name": "Segment", "length_m": 1}],
        )
        cable = operation(db, actor, world, ids, "cable")
        label, svg = LabelService(db, actor).create_cable_label(cable.id, "https://sim.example")
        assert {row.tenant_id for row in (room, rack, device, pathway, cable, label)} == {
            world.tenant_a
        }
        assert (
            label.standard_profile_id == db.get(Tenant, world.tenant_a).active_standard_profile_id
        )
        assert "svg" in svg
        db.commit()


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_shared_membership_role_controls_real_api_writes(mutation_world, role):
    world, ids, client = mutation_world
    with world.scoped_session() as db:
        member = db.scalar(
            select(TenantMembership).where(TenantMembership.user_id == world.supervisor)
        )
        member.role = role
        member.permissions = VIEWER_PERMISSIONS if role == "viewer" else EDITOR_PERMISSIONS
        db.commit()
    headers = {
        "X-Tenant-ID": str(world.tenant_a),
        "X-Actor-ID": str(world.supervisor),
        "X-Project-ID": str(world.project),
        "X-Location-ID": str(world.building),
    }
    response = client.post(
        "/api/v1/locations",
        headers=headers,
        json={
            "location_type": "room",
            "identifier": "ROLE-ROOM",
            "name": "Room",
            "parent_id": str(world.building),
        },
    )
    assert response.status_code == (201 if role == "editor" else 403), response.text
    if role == "editor":
        foreign = client.post(
            "/api/v1/cables",
            headers=headers,
            json={
                "identifier": "ROLE-CABLE",
                "media_type": "copper",
                "construction": "patch_cord",
                "port_a_id": str(ids["port_a"]),
                "port_b_id": str(ids["port_b"]),
                "project_id": str(ids["foreign"]["project"]),
            },
        )
        assert foreign.status_code == 404


@pytest.mark.parametrize("name", ["parent", "cable", "label", "trace"])
def test_revoked_grant_rejects_reused_service_principal(mutation_world, name):
    world, ids, _ = mutation_world
    with world.scoped_session() as db:
        actor = principal(db, world)
        grant = db.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        grant.status = AccessGrantStatus.REVOKED
        db.commit()
        with pytest.raises(AuthorizationError):
            operation(
                db, actor, world, ids, name, {"location": world.tr, "cable": world.horizontal_cable}
            )


def test_personal_owner_boundary_overrides_existing_editor_membership(mutation_world):
    world, ids, client = mutation_world
    with world.scoped_session() as db:
        member = db.scalar(
            select(TenantMembership).where(TenantMembership.user_id == world.supervisor)
        )
        member.permissions = list(EDITOR_PERMISSIONS)
        db.commit()
        old_actor = principal(db, world, world.supervisor)
        tenant = db.get(Tenant, world.tenant_a)
        tenant.workspace_kind = "personal"
        tenant.workspace_owner_id = world.admin
        db.commit()
        with pytest.raises(AuthorizationError, match="owner"):
            operation(db, old_actor, world, ids, "parent", {"location": world.tr})
        owner = principal(db, world, world.admin)
        assert operation(db, owner, world, ids, "root").tenant_id == world.tenant_a


def test_legacy_trace_checks_connected_edges_beyond_selected_cable(mutation_world):
    world, ids, _ = mutation_world
    with world.scoped_session() as db:
        db.get(Device, world.outlet).location_id = world.other_building
        db.commit()
        actor = principal(db, world)
        # Selected patch cord stays inside the room; its panel connects to an outside cable.
        with pytest.raises(AuthorizationError):
            ConnectivityService(db, actor).trace_cable(world.patch_cord)
        owner = principal(db, world, world.admin)
        assert ConnectivityService(db, owner).trace_cable(world.patch_cord)["complete"]


def test_member_cannot_create_cable_using_corrupt_cross_workspace_location(mutation_world):
    world, ids, _ = mutation_world
    with world.session_factory() as db:
        db.info["bypass_tenant"] = True
        db.get(Device, world.switch).location_id = ids["foreign"]["location"]
        db.commit()
        with pytest.raises(NotFoundError):
            operation(db, principal(db, world, world.admin), world, ids, "cable")


def test_member_role_downgrade_is_seen_by_cached_service_session(mutation_world):
    world, ids, _ = mutation_world
    with world.scoped_session() as db:
        cached_member = db.scalar(
            select(TenantMembership).where(TenantMembership.user_id == world.supervisor)
        )
        cached_member.permissions = list(EDITOR_PERMISSIONS)
        db.commit()
        previous_actor = principal(db, world, world.supervisor)
        assert previous_actor.can("location:create")
        with world.scoped_session() as changing_db:
            current = changing_db.get(TenantMembership, cached_member.id)
            current.permissions = list(VIEWER_PERMISSIONS)
            changing_db.commit()
        with pytest.raises(AuthorizationError, match="Missing permission"):
            operation(db, previous_actor, world, ids, "parent", {"location": world.tr})
        assert not db.new


def _scene_policy(editor, world):
    return editor.update_policy(
        Device,
        world.switch,
        CablePolicyUpdate(
            expected_version=editor.db.get(Device, world.switch).version,
            allows_cables=True,
            allowed_media=["copper"],
        ),
    )


@pytest.mark.parametrize("operation_name", ["read", "write"])
@pytest.mark.parametrize("change", ["revoke", "downgrade", "personal", "identity", "workspace"])
def test_scene_reuse_rechecks_members_and_workspace(mutation_world, operation_name, change):
    world, _, _ = mutation_world
    with world.scoped_session() as db:
        # Retain ORM instances as well as the Principal to reproduce identity-map caching.
        cached_member = db.scalar(
            select(TenantMembership).where(TenantMembership.user_id == world.supervisor)
        )
        cached_member.permissions = list(EDITOR_PERMISSIONS)
        cached_actor = db.get(UserIdentity, world.supervisor)
        cached_workspace = db.get(Tenant, world.tenant_a)
        db.commit()
        actor = principal(db, world, world.supervisor)
        scene = SceneService(db, actor)
        editor = SceneEditorService(db, actor)
        if operation_name == "read":
            assert scene.read(location_id=world.tr)["racks"]
        else:
            assert _scene_policy(editor, world)["id"] == str(world.switch)
            db.commit()
        previous_version = db.get(Device, world.switch).version
        with world.scoped_session() as changing_db:
            if change in {"revoke", "downgrade"}:
                member = changing_db.get(TenantMembership, cached_member.id)
                if change == "revoke":
                    member.active = False
                else:
                    member.permissions = (
                        list(VIEWER_PERMISSIONS) if operation_name == "write" else []
                    )
                    member.role = "viewer"
            elif change == "personal":
                tenant = changing_db.get(Tenant, cached_workspace.id)
                tenant.workspace_kind = "personal"
                tenant.workspace_owner_id = world.admin
            elif change == "identity":
                changing_db.get(UserIdentity, cached_actor.id).active = False
            else:
                changing_db.get(Tenant, cached_workspace.id).active = False
            changing_db.commit()
        with pytest.raises(AuthorizationError):
            if operation_name == "read":
                scene.read(location_id=world.tr)
            else:
                _scene_policy(editor, world)
        assert db.get(Device, world.switch, populate_existing=True).version == previous_version
        assert not db.new
        if change == "downgrade":
            refreshed = scene.principal if operation_name == "read" else editor.principal
            assert refreshed.role == "viewer"
            assert not refreshed.can("device:update")


@pytest.mark.parametrize("operation_name", ["policy", "preview"])
@pytest.mark.parametrize("actor_kind", ["member", "contractor"])
def test_scene_cannot_trust_forged_member_permissions(mutation_world, operation_name, actor_kind):
    world, ids, _ = mutation_world
    with world.scoped_session() as db:
        actor_id = world.supervisor if actor_kind == "member" else world.contractor
        if actor_kind == "member":
            member = db.scalar(select(TenantMembership).where(TenantMembership.user_id == actor_id))
            member.permissions = list(VIEWER_PERMISSIONS)
            member.role = "viewer"
        else:
            grant = db.scalar(select(AccessGrant).where(AccessGrant.subject_user_id == actor_id))
            grant.permissions = list(VIEWER_PERMISSIONS)
        db.commit()
        forged = replace(
            principal(db, world, actor_id),
            permissions=frozenset({"*"}),
            is_tenant_member=True,
        )
        editor = SceneEditorService(db, forged)
        with pytest.raises(AuthorizationError, match="Missing permission"):
            if operation_name == "policy":
                _scene_policy(editor, world)
            else:
                SceneRouteService(editor).preview(
                    RoutePreview(
                        port_a_id=ids["port_a"], port_b_id=ids["port_b"], media_type="copper"
                    )
                )
        assert editor.principal.permissions == frozenset(VIEWER_PERMISSIONS)
        assert not db.new


def test_scene_refresh_keeps_read_only_capabilities_and_original_context(mutation_world):
    world, _, _ = mutation_world
    with world.scoped_session() as db:
        member = db.scalar(
            select(TenantMembership).where(TenantMembership.user_id == world.supervisor)
        )
        member.permissions = list(EDITOR_PERMISSIONS)
        db.commit()
        scene = SceneService(db, principal(db, world, world.supervisor))
        scene.read(location_id=world.tr)
        with world.scoped_session() as changing_db:
            current = changing_db.get(TenantMembership, member.id)
            current.role = "viewer"
            current.permissions = list(VIEWER_PERMISSIONS)
            changing_db.commit()
        # Reading a child does not narrow the service's selected context for its next read.
        data = scene.read(location_id=world.building)
        assert data["scope"]["location_id"] == str(world.building)
        assert scene.principal.location_id == world.building
        assert scene.principal.role == "viewer"
        assert scene.principal.permissions == frozenset(VIEWER_PERMISSIONS)
        assert not scene.principal.can("device:update")


@pytest.mark.parametrize("change", ["device_location", "room_parent"])
def test_scene_reuse_refreshes_cached_resource_scope(mutation_world, change):
    world, _, _ = mutation_world
    with world.scoped_session() as db:
        editor = SceneEditorService(db, principal(db, world))
        cached_device = db.get(Device, world.switch)
        cached_room = db.get(Location, world.tr)
        assert _scene_policy(editor, world)["id"] == str(world.switch)
        db.commit()
        with world.scoped_session() as changing_db:
            if change == "device_location":
                changing_db.get(Device, cached_device.id).location_id = world.other_building
            else:
                changing_db.get(Location, cached_room.id).parent_id = world.other_building
            changing_db.commit()
        with pytest.raises(AuthorizationError):
            _scene_policy(editor, world)


def test_scene_personal_owner_retains_read_and_write_access(mutation_world):
    world, _, _ = mutation_world
    with world.scoped_session() as db:
        actor = principal(db, world, world.admin)
        scene = SceneService(db, actor)
        editor = SceneEditorService(db, actor)
        tenant = db.get(Tenant, world.tenant_a)
        tenant.workspace_kind = "personal"
        tenant.workspace_owner_id = world.admin
        db.commit()
        assert scene.read(location_id=world.tr)["racks"]
        assert _scene_policy(editor, world)["id"] == str(world.switch)

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import deps
from app.api.scene import build_scene_router
from app.exceptions import DomainError
from app.models import (
    AccessGrant,
    Cable,
    CableRouteSegment,
    Device,
    Location,
    LocationType,
    Pathway,
    PathwaySegment,
    Port,
    Project,
    Rack,
    utcnow,
)
from app.security import resolve_principal
from app.services.scene import SceneService


@pytest.fixture
def scene_client(world, monkeypatch):
    monkeypatch.setattr(deps, "SessionLocal", world.session_factory)
    app = FastAPI()
    app.include_router(build_scene_router(deps.get_db, deps.get_principal), prefix="/api/v1")

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, error: DomainError):
        return JSONResponse(status_code=error.status_code, content={"detail": str(error)})

    return TestClient(app)


def headers(world, *, location=None, actor=None):
    return {
        "X-Tenant-ID": str(world.tenant_a),
        "X-Actor-ID": str(actor or world.admin),
        "X-Project-ID": str(world.project),
        "X-Location-ID": str(location or world.tr),
    }


def test_scene_uses_header_scope_and_redacts_external_endpoints(world, scene_client):
    with world.scoped_session() as session:
        session.get(Device, world.outlet).location_id = world.other_building
        hidden_port = session.scalar(select(Port.id).where(Port.device_id == world.outlet))
        session.commit()
    response = scene_client.get("/api/v1/scene", headers=headers(world))
    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == {
        "tenant_id": str(world.tenant_a),
        "project_id": str(world.project),
        "location_id": str(world.tr),
        "include_descendants": True,
    }
    assert [row["id"] for row in data["locations"]] == [str(world.tr)]
    assert [row["id"] for row in data["racks"]] == [str(world.rack)]
    assert data["racks"][0]["position_x"] == 0  # Preserve source coordinates.
    assert {row["id"] for row in data["devices"]} == {str(world.panel), str(world.switch)}
    horizontal = next(row for row in data["cables"] if row["id"] == str(world.horizontal_cable))
    assert horizontal["endpoint_scope"] == "partial"
    assert horizontal["route_scope"] == "none"
    assert len(horizontal["terminations"]) == 1
    assert str(world.outlet) not in response.text
    assert str(world.other_building) not in response.text
    assert str(hidden_port) not in response.text
    assert str(world.private_rack) not in response.text
    assert data["truncated"] == []


def test_scene_includes_descendants_and_complete_cable_when_visible(world, scene_client):
    response = scene_client.get("/api/v1/scene", headers=headers(world, location=world.building))
    assert response.status_code == 200
    data = response.json()
    assert {row["id"] for row in data["locations"]} == {str(world.building), str(world.tr)}
    assert str(world.outlet) in {row["id"] for row in data["devices"]}
    assert all(row["endpoint_scope"] == "complete" for row in data["cables"])


@pytest.mark.parametrize("query", ["location_id", "project_id"])
def test_scene_query_cannot_expand_header_context(world, scene_client, query):
    value = world.building if query == "location_id" else uuid.uuid4()
    response = scene_client.get(f"/api/v1/scene?{query}={value}", headers=headers(world))
    assert response.status_code == 403


def test_scene_cross_tenant_location_is_not_found(world, scene_client):
    with world.session_factory() as session:
        private_location = session.get(Rack, world.private_rack).location_id
    response = scene_client.get(
        f"/api/v1/scene?location_id={private_location}",
        headers={
            "X-Tenant-ID": str(world.tenant_a),
            "X-Actor-ID": str(world.admin),
        },
    )
    assert response.status_code == 404


def test_scene_requires_every_exposed_resource_permission(world, scene_client):
    denied = scene_client.get("/api/v1/scene", headers=headers(world, actor=world.supervisor))
    assert denied.status_code == 403
    with world.scoped_session() as session:
        grant = session.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        grant.permissions = [*grant.permissions, "location:read", "pathway:read"]
        session.commit()
    contractor_headers = headers(world, actor=world.contractor)
    allowed = scene_client.get("/api/v1/scene", headers=contractor_headers)
    assert allowed.status_code == 200
    assert str(world.outlet) not in allowed.text
    outside = scene_client.get(
        f"/api/v1/scene?location_id={world.other_building}", headers=contractor_headers
    )
    assert outside.status_code == 403


def test_scene_returns_only_visible_segments_and_project_cables(world, scene_client):
    with world.scoped_session() as session:
        pathway = Pathway(
            tenant_id=world.tenant_a,
            location_id=world.tr,
            identifier="TRAY-ROOM",
            name="Room tray",
            pathway_type="basket_tray",
        )
        outside = Pathway(
            tenant_id=world.tenant_a,
            location_id=world.building,
            identifier="TRAY-OUTSIDE",
            name="Outside tray",
            pathway_type="basket_tray",
        )
        session.add_all([pathway, outside])
        session.flush()
        segment = PathwaySegment(
            tenant_id=world.tenant_a,
            pathway_id=pathway.id,
            sequence=1,
            name="Visible route",
            coordinates=[{"x": 1.0, "y": 2.0, "z": 2.8}, {"x": 4.0, "y": 2.0, "z": 2.8}],
        )
        hidden_segment = PathwaySegment(
            tenant_id=world.tenant_a,
            pathway_id=outside.id,
            sequence=1,
            name="Hidden route",
            coordinates=[{"x": 20.0, "y": 30.0, "z": 2.8}],
        )
        project = session.get(Project, world.project)
        other_project = Project(
            tenant_id=world.tenant_a,
            project_number="OTHER",
            name="Other project",
            customer_organization_id=project.customer_organization_id,
        )
        session.add_all([segment, hidden_segment, other_project])
        session.flush()
        session.add_all(
            [
                CableRouteSegment(
                    tenant_id=world.tenant_a,
                    cable_id=world.horizontal_cable,
                    pathway_segment_id=segment.id,
                    sequence=1,
                ),
                CableRouteSegment(
                    tenant_id=world.tenant_a,
                    cable_id=world.horizontal_cable,
                    pathway_segment_id=hidden_segment.id,
                    sequence=2,
                ),
            ]
        )
        session.get(Cable, world.patch_cord).project_id = other_project.id
        session.commit()
        expected_coordinates = segment.coordinates
        segment_id, hidden_id = str(segment.id), str(hidden_segment.id)
    response = scene_client.get("/api/v1/scene", headers=headers(world))
    assert response.status_code == 200
    data = response.json()
    assert [row["id"] for row in data["cables"]] == [str(world.horizontal_cable)]
    assert data["cables"][0]["route_segment_ids"] == [segment_id]
    assert data["cables"][0]["route_scope"] == "partial"
    assert data["pathways"][0]["segments"][0]["coordinates"] == expected_coordinates
    assert hidden_id not in response.text


def test_scene_guards_unscoped_sessions_and_reports_truncation(world):
    with world.session_factory() as session:
        principal = resolve_principal(session, actor_id=world.admin, tenant_id=world.tenant_a)
        service = SceneService(session, principal)
        service.MAX_DEVICES = 1
        data = service.read()
        assert all(row["tenant_id"] == str(world.tenant_a) for row in data["racks"])
        assert len(data["devices"]) == 1
        assert "devices" in data["truncated"]
        assert str(world.private_rack) not in str(data)
        assert all(port["device_id"] == data["devices"][0]["id"] for port in data["ports"])


def test_scene_omits_soft_deleted_resources_even_with_bypass_session(world):
    with world.session_factory() as session:
        principal = resolve_principal(session, actor_id=world.admin, tenant_id=world.tenant_a)
        session.get(Device, world.switch).deleted_at = utcnow()
        session.commit()
        session.info["bypass_tenant"] = True
        data = SceneService(session, replace(principal, location_id=world.tr)).read()
        assert str(world.switch) not in str(data)
        assert {row["id"] for row in data["devices"]} == {str(world.panel)}


@pytest.fixture
def multi_floor_world(world):
    floors = {}
    with world.scoped_session() as db:
        for building_name, building_id in [("A", world.building), ("B", world.other_building)]:
            for number in (1, 2):
                key = f"{building_name}{number}"
                floor = Location(
                    tenant_id=world.tenant_a,
                    parent_id=building_id,
                    location_type=LocationType.FLOOR,
                    identifier=f"FLOOR-{key}",
                    name=f"Floor {key}",
                )
                db.add(floor)
                db.flush()
                room = Location(
                    tenant_id=world.tenant_a,
                    parent_id=floor.id,
                    location_type=LocationType.ROOM,
                    identifier=f"ROOM-{key}",
                    name=f"Room {key}",
                )
                pathway = Pathway(
                    tenant_id=world.tenant_a,
                    location_id=floor.id,
                    identifier=f"TRAY-{key}",
                    name=f"Floor tray {key}",
                    pathway_type="basket_tray",
                )
                db.add_all([room, pathway])
                db.flush()
                rack = Rack(
                    tenant_id=world.tenant_a,
                    location_id=room.id,
                    rack_identifier=f"RACK-{key}",
                    name=f"Rack {key}",
                )
                segment = PathwaySegment(
                    tenant_id=world.tenant_a,
                    pathway_id=pathway.id,
                    sequence=1,
                    name=f"Segment {key}",
                    coordinates=[{"x": 1, "y": 1, "z": 2}, {"x": 2, "y": 1, "z": 2}],
                )
                db.add_all([rack, segment])
                db.flush()
                floors[key] = {
                    "floor": floor.id,
                    "room": room.id,
                    "rack": rack.id,
                    "pathway": pathway.id,
                    "segment": segment.id,
                }
        db.get(Location, world.tr).parent_id = floors["A1"]["floor"]
        db.get(Device, world.outlet).location_id = floors["A2"]["room"]
        db.commit()
    return floors


@pytest.mark.parametrize("key", ["A1", "A2", "B1", "B2"])
def test_selected_floor_excludes_other_floors_and_buildings(
    world, scene_client, multi_floor_world, key
):
    chosen = multi_floor_world[key]
    response = scene_client.get("/api/v1/scene", headers=headers(world, location=chosen["floor"]))
    assert response.status_code == 200, response.text
    data = response.json()
    expected_locations = {str(chosen["floor"]), str(chosen["room"])}
    expected_racks = {str(chosen["rack"])}
    if key == "A1":
        expected_locations.add(str(world.tr))
        expected_racks.add(str(world.rack))
    assert {row["id"] for row in data["locations"]} == expected_locations
    assert {row["id"] for row in data["racks"]} == expected_racks
    assert {row["id"] for row in data["pathways"]} == {str(chosen["pathway"])}
    for other_key, objects in multi_floor_world.items():
        if other_key != key:
            assert all(str(value) not in response.text for value in objects.values())


@pytest.mark.parametrize("root_kind", ["floor", "room"])
@pytest.mark.parametrize("nested_kind", [LocationType.FLOOR, LocationType.BUILDING])
def test_physical_scene_stops_at_nested_floor_or_building(
    world, scene_client, multi_floor_world, root_kind, nested_kind
):
    chosen = multi_floor_world["A1"]
    with world.scoped_session() as db:
        nested = Location(
            tenant_id=world.tenant_a,
            parent_id=chosen["room"],
            location_type=nested_kind,
            identifier="WRONG-NESTING",
            name="Unexpected floor or building",
        )
        db.add(nested)
        db.flush()
        child = Location(
            tenant_id=world.tenant_a,
            parent_id=nested.id,
            location_type=LocationType.ROOM,
            identifier="WRONG-CHILD",
            name="Room behind boundary",
        )
        db.add(child)
        db.flush()
        rack = Rack(
            tenant_id=world.tenant_a,
            location_id=child.id,
            rack_identifier="WRONG-RACK",
            name="Rack behind boundary",
        )
        db.add(rack)
        db.commit()
        hidden_ids = [str(nested.id), str(child.id), str(rack.id)]
    response = scene_client.get("/api/v1/scene", headers=headers(world, location=chosen[root_kind]))
    assert response.status_code == 200, response.text
    assert all(value not in response.text for value in hidden_ids)
    assert str(chosen["rack"]) in response.text
    # Legacy building aggregation remains available to existing API consumers.
    broad = scene_client.get("/api/v1/scene", headers=headers(world, location=world.building))
    assert broad.status_code == 200
    assert all(value in broad.text for value in hidden_ids)


@pytest.mark.parametrize(
    "route_kind, expected_scope, visible_count",
    [
        ("both", "partial", 1),
        ("outside_only", "partial", 0),
        ("local_only", "complete", 1),
        ("none", "none", 0),
        ("deleted", "complete", 1),
        ("capped", "partial", 0),
    ],
)
def test_route_scope_reports_clipped_cross_floor_routes(
    world, scene_client, multi_floor_world, monkeypatch, route_kind, expected_scope, visible_count
):
    local, remote = multi_floor_world["A1"], multi_floor_world["A2"]
    with world.scoped_session() as db:
        for sequence, floor in enumerate([local, remote], start=1):
            db.add(
                CableRouteSegment(
                    tenant_id=world.tenant_a,
                    cable_id=world.horizontal_cable,
                    pathway_segment_id=floor["segment"],
                    sequence=sequence,
                )
            )
        segments = []
        if route_kind in {"both", "local_only", "deleted", "capped"}:
            segments.append(local["segment"])
        if route_kind in {"both", "outside_only", "deleted"}:
            segments.append(remote["segment"])
        for sequence, segment_id in enumerate(segments, start=1):
            db.add(
                CableRouteSegment(
                    tenant_id=world.tenant_a,
                    cable_id=world.patch_cord,
                    pathway_segment_id=segment_id,
                    sequence=sequence,
                    deleted_at=utcnow()
                    if route_kind == "deleted" and segment_id == remote["segment"]
                    else None,
                )
            )
        hidden_port = db.scalar(select(Port.id).where(Port.device_id == world.outlet))
        db.commit()
    if route_kind == "capped":
        monkeypatch.setattr(SceneService, "MAX_SEGMENTS", 0)
    response = scene_client.get("/api/v1/scene", headers=headers(world, location=local["floor"]))
    assert response.status_code == 200, response.text
    cables = {row["id"]: row for row in response.json()["cables"]}
    cross_floor = cables[str(world.horizontal_cable)]
    assert cross_floor["endpoint_scope"] == "partial"
    assert cross_floor["route_scope"] == "partial"
    assert len(cross_floor["terminations"]) == 1
    assert str(world.outlet) not in response.text
    assert str(hidden_port) not in response.text
    assert all(str(value) not in response.text for value in remote.values())
    # Both ends can be on this floor even though their registered route leaves it.
    same_floor = cables[str(world.patch_cord)]
    assert same_floor["endpoint_scope"] == "complete"
    assert same_floor["route_scope"] == expected_scope
    assert len(same_floor["route_segment_ids"]) == visible_count


def test_port_pages_are_complete_and_cables_survive_endpoints_outside_first_page(
    world, scene_client, monkeypatch
):
    # Empty, lowest UUID guarantees both existing cable endpoints fall on later pages.
    with world.scoped_session() as db:
        db.add(
            Port(
                id=uuid.UUID(int=1),
                tenant_id=world.tenant_a,
                device_id=world.switch,
                identifier="PAGING-FIRST",
                label="Unused",
                connector_type="RJ45",
                media_type="copper",
                front_or_rear="front",
                position_index=99,
            )
        )
        db.commit()
    monkeypatch.setattr(SceneService, "MAX_PORTS", 1)
    response = scene_client.get("/api/v1/scene", headers=headers(world))
    assert response.status_code == 200, response.text
    scene = response.json()
    assert [p["id"] for p in scene["ports"]] == [str(uuid.UUID(int=1))]
    assert "ports" in scene["truncated"]
    cables = {row["id"]: row for row in scene["cables"]}
    assert cables[str(world.patch_cord)]["endpoint_scope"] == "complete"
    assert len(cables[str(world.patch_cord)]["terminations"]) == 2
    ids = {scene["ports"][0]["id"]}
    ports = list(scene["ports"])
    cursor = scene["port_page"]["next_cursor"]
    while cursor:
        response = scene_client.get(
            f"/api/v1/scene/ports?after={cursor}&limit=2", headers=headers(world)
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["port_page"]["total"] == scene["port_page"]["total"]
        new_ids = {p["id"] for p in page["ports"]}
        assert not ids & new_ids
        ids.update(new_ids)
        ports.extend(page["ports"])
        cursor = page["port_page"]["next_cursor"]
    assert len(ids) == scene["port_page"]["total"]
    connected = {term["port_id"] for cable in scene["cables"] for term in cable["terminations"]}
    assert connected <= {p["id"] for p in ports if p["occupied"]}
    assert scene["ports"][0]["occupied"] is False


@pytest.mark.parametrize("query", ["location_id", "project_id"])
def test_port_pagination_cannot_expand_scope(world, scene_client, query):
    value = world.building if query == "location_id" else uuid.uuid4()
    assert (
        scene_client.get(
            f"/api/v1/scene/ports?{query}={value}&limit=2", headers=headers(world)
        ).status_code
        == 403
    )


@pytest.mark.parametrize("limit", [0, -1, 10001])
def test_port_page_size_is_bounded(world, scene_client, limit):
    assert (
        scene_client.get(f"/api/v1/scene/ports?limit={limit}", headers=headers(world)).status_code
        == 422
    )


def test_port_page_rechecks_permissions_after_first_page(world, scene_client):
    from app.models import TenantMembership

    assert (
        scene_client.get("/api/v1/scene/ports?limit=1", headers=headers(world)).status_code == 200
    )
    with world.scoped_session() as db:
        member = db.scalar(select(TenantMembership).where(TenantMembership.user_id == world.admin))
        member.permissions = ["cable:read"]
        db.commit()
    assert (
        scene_client.get("/api/v1/scene/ports?limit=1", headers=headers(world)).status_code == 403
    )

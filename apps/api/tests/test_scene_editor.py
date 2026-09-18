from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import deps
from app.fiber_models import PhysicalPortClaim
from app.main import app
from app.models import (
    AuditEvent,
    Cable,
    Device,
    DeviceTemplate,
    Location,
    Pathway,
    PathwaySegment,
    Port,
    Rack,
    TenantMembership,
)


@pytest.fixture
def editor(world, monkeypatch):
    monkeypatch.setattr(deps, "SessionLocal", world.session_factory)
    return TestClient(app)


def headers(world, *, location=None, actor=None):
    return {
        "X-Tenant-ID": str(world.tenant_a),
        "X-Actor-ID": str(actor or world.admin),
        "X-Project-ID": str(world.project),
        "X-Location-ID": str(location or world.tr),
    }


@pytest.fixture
def room(world, editor):
    response = editor.post(
        "/api/v1/scene/rooms",
        headers=headers(world),
        json={
            "parent_id": str(world.tr),
            "identifier": "ROOM-3D",
            "name": "3D room",
            "kind": "server_room",
            "width_m": 10,
            "depth_m": 8,
            "height_m": 3.2,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def grid(room, **changes):
    return {
        "location_id": room["id"],
        "identifier_prefix": "NEW-R",
        "name_prefix": "Rack",
        "count": 1,
        "columns": 1,
        "position_x": 1,
        "position_y": 1,
        "rotation": 0,
        "gap_m": 0.8,
        "width_mm": 600,
        "depth_mm": 1000,
        "height_u": 42,
        **changes,
    }


def create_racks(world, editor, room, **changes):
    response = editor.post(
        "/api/v1/scene/racks", headers=headers(world), json=grid(room, **changes)
    )
    assert response.status_code == 201, response.text
    return response.json()["racks"]


def device_payload(world, rack, identifier="DEVICE-NEW", start=1):
    return {
        "rack_id": rack["id"],
        "template_id": str(world.switch_template),
        "identifier": identifier,
        "name": identifier,
        "start_u": start,
        "face": "front",
    }


def test_room_create_and_reload_and_top_level_scope(world, editor, room):
    assert room["location_type"] == "data_hall"
    assert {key: room["dimensions"][key] for key in ("width_m", "depth_m", "height_m")} == {
        "width_m": 10,
        "depth_m": 8,
        "height_m": 3.2,
    }
    entrance = room["dimensions"]["entrances"][0]
    assert {key: entrance[key] for key in ("wall", "offset_m", "width_m", "height_m")} == {
        "wall": "south",
        "offset_m": 4.5,
        "width_m": 1.0,
        "height_m": 2.1,
    }
    scene = editor.get("/api/v1/scene", headers=headers(world)).json()
    assert room["id"] in {row["id"] for row in scene["locations"]}
    payload = {
        "identifier": "TOP-ROOM",
        "name": "Top room",
        "kind": "room",
        "width_m": 6,
        "depth_m": 4,
        "height_m": 3,
    }
    denied = editor.post("/api/v1/scene/rooms", headers=headers(world), json=payload)
    assert denied.status_code == 403
    owner_headers = {"X-Tenant-ID": str(world.tenant_a), "X-Actor-ID": str(world.admin)}
    created = editor.post("/api/v1/scene/rooms", headers=owner_headers, json=payload)
    assert created.status_code == 201
    assert created.json()["parent_id"] is None


def test_room_parent_and_write_permissions_are_scoped(world, editor):
    payload = {
        "parent_id": str(world.other_building),
        "identifier": "OUTSIDE-ROOM",
        "name": "Room",
        "width_m": 6,
        "depth_m": 4,
        "height_m": 3,
    }
    assert (
        editor.post("/api/v1/scene/rooms", headers=headers(world), json=payload).status_code == 403
    )
    with world.scoped_session() as session:
        member = session.scalar(
            select(TenantMembership).where(TenantMembership.user_id == world.supervisor)
        )
        member.permissions = [
            f"{name}:read" for name in ("location", "rack", "device", "port", "pathway", "cable")
        ]
        session.commit()
    payload["parent_id"] = str(world.tr)
    response = editor.post(
        "/api/v1/scene/rooms", headers=headers(world, actor=world.supervisor), json=payload
    )
    assert response.status_code == 403
    assert "location:create" in response.text


def test_rack_grid_persists_real_coordinates_and_duplicate_is_atomic(world, editor, room):
    racks = create_racks(world, editor, room, count=4, columns=2)
    assert [row["rack_identifier"] for row in racks] == [f"NEW-R-{i:02d}" for i in range(1, 5)]
    assert [(row["position_x"], row["position_y"]) for row in racks] == [
        (1, 1),
        (2.4, 1),
        (1, 2.8),
        (2.4, 2.8),
    ]
    response = editor.post(
        "/api/v1/scene/racks",
        headers=headers(world),
        json=grid(room, count=4, columns=2, position_x=5),
    )
    assert response.status_code == 409
    with world.scoped_session() as session:
        rows = session.scalars(select(Rack).where(Rack.location_id == uuid.UUID(room["id"]))).all()
        assert len(rows) == 4
        assert {row.version for row in rows} == {1}
    refreshed = editor.get(f"/api/v1/scene?location_id={room['id']}", headers=headers(world)).json()
    assert {row["id"] for row in refreshed["racks"]} == {row["id"] for row in racks}


def test_rack_batch_failure_after_first_insert_rolls_back_records_and_audit(world, editor, room):
    with world.scoped_session() as session:
        session.add(
            Rack(
                tenant_id=world.tenant_a,
                location_id=world.tr,
                rack_identifier="NEW-R-02",
                name="Existing collision",
            )
        )
        session.commit()
        audit_count = session.scalar(select(func.count(AuditEvent.id)))
    response = editor.post(
        "/api/v1/scene/racks", headers=headers(world), json=grid(room, count=3, columns=3)
    )
    assert response.status_code == 409
    with world.scoped_session() as session:
        assert (
            session.scalar(
                select(func.count(Rack.id)).where(Rack.location_id == uuid.UUID(room["id"]))
            )
            == 0
        )
        assert session.scalar(select(func.count(AuditEvent.id))) == audit_count


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"count": 12, "columns": 6, "position_x": 8}, 422),
        ({"position_x": 0}, 422),
        ({"position_y": 0.2, "rotation": 45}, 422),
        ({"height_u": 60}, 201),
        ({"count": True}, 422),
        ({"position_x": "Infinity"}, 422),
    ],
)
def test_rack_boundary_and_finite_validation(world, editor, room, changes, status):
    response = editor.post(
        "/api/v1/scene/racks", headers=headers(world), json=grid(room, **changes)
    )
    assert response.status_code == status, response.text
    if status != 201:
        with world.scoped_session() as session:
            assert (
                session.scalar(
                    select(func.count(Rack.id)).where(Rack.location_id == uuid.UUID(room["id"]))
                )
                == 0
            )


def test_rack_overlap_rotation_and_version_save_reload(world, editor, room):
    first = create_racks(world, editor, room, position_x=2, position_y=2, rotation=45)[0]
    collision = editor.post(
        "/api/v1/scene/racks",
        headers=headers(world),
        json=grid(room, identifier_prefix="OTHER", position_x=2.4, position_y=2, rotation=-45),
    )
    assert collision.status_code == 409
    body = {
        "expected_version": first["version"],
        "position_x": 4,
        "position_y": 3,
        "position_z": 0,
        "rotation": 90,
    }
    moved = editor.patch(f"/api/v1/scene/racks/{first['id']}", headers=headers(world), json=body)
    assert moved.status_code == 200, moved.text
    assert moved.json()["version"] == first["version"] + 1
    stale = editor.patch(
        f"/api/v1/scene/racks/{first['id']}", headers=headers(world), json={**body, "position_x": 5}
    )
    assert stale.status_code == 409
    outside = editor.patch(
        f"/api/v1/scene/racks/{first['id']}",
        headers=headers(world),
        json={**body, "expected_version": moved.json()["version"], "position_x": 0},
    )
    assert outside.status_code == 422
    with world.scoped_session() as session:
        rack = session.get(Rack, uuid.UUID(first["id"]))
        assert (rack.position_x, rack.position_y, rack.rotation, rack.version) == (4, 3, 90, 2)
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "rack.position_updated")
        )
        assert audit.before["position_x"] == 2 and audit.after["position_x"] == 4


def test_unknown_room_dimensions_and_foreign_rack_are_rejected(world, editor, room):
    payload = grid({"id": str(world.tr)})
    assert (
        editor.post("/api/v1/scene/racks", headers=headers(world), json=payload).status_code == 422
    )
    pose = {"expected_version": 1, "position_x": 2, "position_y": 2, "position_z": 0, "rotation": 0}
    response = editor.patch(
        f"/api/v1/scene/racks/{world.private_rack}", headers=headers(world), json=pose
    )
    assert response.status_code == 404


def test_device_creation_keeps_u_constraints_and_generates_ports(world, editor, room):
    rack = create_racks(world, editor, room)[0]
    payload = device_payload(world, rack)
    created = editor.post("/api/v1/scene/devices", headers=headers(world), json=payload)
    assert created.status_code == 201, created.text
    overlap = editor.post(
        "/api/v1/scene/devices", headers=headers(world), json={**payload, "identifier": "OVERLAP"}
    )
    assert overlap.status_code == 409
    overflow = editor.post(
        "/api/v1/scene/devices",
        headers=headers(world),
        json={**payload, "identifier": "TOO-TALL", "start_u": 60},
    )
    assert overflow.status_code == 422
    with world.scoped_session() as session:
        assert (
            session.scalar(
                select(func.count(Device.id)).where(Device.rack_id == uuid.UUID(rack["id"]))
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count(Port.id)).where(Port.device_id == uuid.UUID(created.json()["id"]))
            )
            > 0
        )


def pathway_payload(room, **changes):
    return {
        "location_id": room["id"],
        "identifier": "NEW-TRAY",
        "name": "Cable tray",
        "pathway_type": "basket_tray",
        "segments": [
            {
                "name": "Main run",
                "coordinates": [{"x": 1, "y": 1, "z": 2.5}, {"x": 5, "y": 1, "z": 2.5}],
            }
        ],
        **changes,
    }


def test_pathway_coordinates_length_and_segment_validation(world, editor, room):
    created = editor.post(
        "/api/v1/scene/pathways", headers=headers(world), json=pathway_payload(room)
    )
    assert created.status_code == 201, created.text
    with world.scoped_session() as session:
        segment = session.scalar(
            select(PathwaySegment).where(
                PathwaySegment.pathway_id == uuid.UUID(created.json()["id"])
            )
        )
        assert segment.sequence == 1 and segment.length_m == 4
    for coordinates in (
        [{"x": 1, "y": 1}],
        [{"x": 1, "y": 1}, {"x": 100, "y": 1}],
        [{"x": 1, "y": 1}, {"x": "NaN", "y": 1}],
        [{"x": 1, "y": 1}, {"x": 1, "y": 1}],
    ):
        response = editor.post(
            "/api/v1/scene/pathways",
            headers=headers(world),
            json=pathway_payload(
                room,
                identifier="BAD-TRAY",
                segments=[{"name": "Invalid", "coordinates": coordinates}],
            ),
        )
        assert response.status_code == 422, response.text
    with world.scoped_session() as session:
        assert (
            session.scalar(
                select(func.count(Pathway.id)).where(Pathway.location_id == uuid.UUID(room["id"]))
            )
            == 1
        )


def test_cable_creation_scope_occupation_route_and_reload(world, editor, room):
    racks = create_racks(world, editor, room, count=2, columns=2)
    device_ids = []
    for index, rack in enumerate(racks):
        response = editor.post(
            "/api/v1/scene/devices",
            headers=headers(world),
            json=device_payload(world, rack, identifier=f"DEVICE-{index}"),
        )
        assert response.status_code == 201
        device_ids.append(uuid.UUID(response.json()["id"]))
    tray = editor.post(
        "/api/v1/scene/pathways", headers=headers(world), json=pathway_payload(room)
    ).json()
    with world.scoped_session() as session:
        ports = [
            session.scalars(
                select(Port).where(Port.device_id == device).order_by(Port.position_index)
            ).all()
            for device in device_ids
        ]
        port_a, port_b = str(ports[0][0].id), str(ports[1][0].id)
        free_a, free_b = str(ports[0][1].id), str(ports[1][1].id)
        outside_port = str(session.scalar(select(Port.id).where(Port.device_id == world.outlet)))
        segment_id = str(
            session.scalar(
                select(PathwaySegment.id).where(PathwaySegment.pathway_id == uuid.UUID(tray["id"]))
            )
        )
    payload = {
        "identifier": "NEW-LINK",
        "media_type": "Cat6A copper",
        "construction": "patch_cord",
        "port_a_id": port_a,
        "port_b_id": port_b,
        "route_segment_ids": [segment_id],
    }
    created = editor.post("/api/v1/scene/cables", headers=headers(world), json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["project_id"] == str(world.project)
    duplicate = editor.post(
        "/api/v1/scene/cables", headers=headers(world), json={**payload, "identifier": "DUPLICATE"}
    )
    assert duplicate.status_code == 409
    cross_scope = editor.post(
        "/api/v1/scene/cables",
        headers=headers(world),
        json={**payload, "identifier": "HIDDEN", "port_a_id": free_a, "port_b_id": outside_port},
    )
    assert cross_scope.status_code == 403
    cross_project = editor.post(
        "/api/v1/scene/cables",
        headers=headers(world),
        json={
            **payload,
            "identifier": "OTHER-PROJECT",
            "port_a_id": free_a,
            "port_b_id": free_b,
            "project_id": str(uuid.uuid4()),
        },
    )
    assert cross_project.status_code == 403
    scene = editor.get(f"/api/v1/scene?location_id={room['id']}", headers=headers(world)).json()
    cable = scene["cables"][0]
    assert cable["id"] == created.json()["id"] and cable["endpoint_scope"] == "complete"
    assert cable["route_segment_ids"] == [segment_id]
    assert {row["port_id"] for row in cable["terminations"]} == {port_a, port_b}
    assert all(port["occupied"] for port in scene["ports"] if port["id"] in {port_a, port_b})
    assert all(not port["occupied"] for port in scene["ports"] if port["id"] in {free_a, free_b})
    with world.scoped_session() as session:
        assert (
            session.scalar(
                select(func.count(Cable.id)).where(
                    Cable.identifier.in_(["DUPLICATE", "HIDDEN", "OTHER-PROJECT"])
                )
            )
            == 0
        )


def test_occupied_flag_covers_fiber_claim_without_exposing_claim_owner(world, editor):
    owner_id = uuid.uuid4()
    with world.scoped_session() as session:
        port = session.scalars(
            select(Port).where(Port.device_id == world.switch).order_by(Port.position_index.desc())
        ).first()
        port_id = str(port.id)
        session.add(
            PhysicalPortClaim(
                tenant_id=world.tenant_a,
                port_id=port.id,
                owner_type="fiber_termination",
                owner_id=owner_id,
            )
        )
        session.commit()
    response = editor.get("/api/v1/scene", headers=headers(world))
    assert response.status_code == 200
    assert next(row for row in response.json()["ports"] if row["id"] == port_id)["occupied"] is True
    assert str(owner_id) not in response.text


def test_scene_editor_non_finite_json_never_becomes_server_error(world, editor):
    response = editor.post(
        "/api/v1/scene/rooms",
        headers={**headers(world), "Content-Type": "application/json"},
        content='{"parent_id":"'
        + str(world.tr)
        + '","identifier":"BAD-FLOAT","name":"Bad float","width_m":NaN,"depth_m":4,"height_m":3}',
    )
    assert response.status_code == 422


def test_device_template_and_pathway_references_cannot_cross_scope(world, editor, room):
    racks = create_racks(world, editor, room, count=2, columns=2)
    with world.session_factory() as session:
        foreign_template = DeviceTemplate(
            tenant_id=world.tenant_b,
            manufacturer="Private",
            model="Secret",
            device_type="switch",
            rack_units=1,
        )
        outside_pathway = Pathway(
            tenant_id=world.tenant_a,
            location_id=world.other_building,
            identifier="HIDDEN-PATHWAY",
            name="Hidden route",
            pathway_type="tray",
        )
        session.add_all([foreign_template, outside_pathway])
        session.flush()
        outside_segment = PathwaySegment(
            tenant_id=world.tenant_a,
            pathway_id=outside_pathway.id,
            sequence=1,
            name="Hidden segment",
            coordinates=[],
        )
        session.add(outside_segment)
        session.commit()
        foreign_id, hidden_id = str(foreign_template.id), str(outside_segment.id)
    response = editor.post(
        "/api/v1/scene/devices",
        headers=headers(world),
        json={**device_payload(world, racks[0]), "template_id": foreign_id},
    )
    assert response.status_code == 404
    device_ids = []
    for index, rack in enumerate(racks):
        device = editor.post(
            "/api/v1/scene/devices",
            headers=headers(world),
            json=device_payload(world, rack, identifier=f"ROUTE-DEVICE-{index}"),
        )
        assert device.status_code == 201
        device_ids.append(uuid.UUID(device.json()["id"]))
    with world.scoped_session() as session:
        port_ids = [
            str(session.scalar(select(Port.id).where(Port.device_id == device).limit(1)))
            for device in device_ids
        ]
    response = editor.post(
        "/api/v1/scene/cables",
        headers=headers(world),
        json={
            "identifier": "BAD-ROUTE",
            "media_type": "Cat6A copper",
            "construction": "patch_cord",
            "port_a_id": port_ids[0],
            "port_b_id": port_ids[1],
            "route_segment_ids": [hidden_id],
        },
    )
    assert response.status_code == 403
    with world.scoped_session() as session:
        assert session.scalar(select(Cable.id).where(Cable.identifier == "BAD-ROUTE")) is None
        assert (
            session.scalar(
                select(PhysicalPortClaim.id).where(
                    PhysicalPortClaim.port_id.in_([uuid.UUID(x) for x in port_ids])
                )
            )
            is None
        )


def test_rack_height_includes_top_frame_and_accepts_exact_upper_bound(world, editor, room):
    with world.scoped_session() as session:
        target = session.get(Location, uuid.UUID(room["id"]))
        target.dimensions = {**target.dimensions, "height_m": 2.0}
        session.commit()
    denied = editor.post(
        "/api/v1/scene/racks", headers=headers(world), json=grid(room, height_u=42)
    )
    assert denied.status_code == 422
    with world.scoped_session() as session:
        assert (
            session.scalar(
                select(func.count(Rack.id)).where(Rack.location_id == uuid.UUID(room["id"]))
            )
            == 0
        )
        target = session.get(Location, uuid.UUID(room["id"]))
        target.dimensions = {**target.dimensions, "height_m": 42 * 0.04445 + 0.1925}
        session.commit()
    accepted = editor.post(
        "/api/v1/scene/racks", headers=headers(world), json=grid(room, height_u=42)
    )
    assert accepted.status_code == 201, accepted.text


@pytest.mark.parametrize("dimensions", [{"width_mm": 499}, {"depth_mm": 449}])
def test_new_racks_cannot_be_smaller_than_rendered_enclosure(world, editor, room, dimensions):
    response = editor.post(
        "/api/v1/scene/racks", headers=headers(world), json=grid(room, **dimensions)
    )
    assert response.status_code == 422
    with world.scoped_session() as session:
        assert (
            session.scalar(
                select(func.count(Rack.id)).where(Rack.location_id == uuid.UUID(room["id"]))
            )
            == 0
        )


def test_legacy_narrow_rack_move_respects_effective_rendered_footprint(world, editor, room):
    with world.scoped_session() as session:
        rack = Rack(
            tenant_id=world.tenant_a,
            location_id=uuid.UUID(room["id"]),
            rack_identifier="LEGACY-NARROW",
            name="Legacy narrow rack",
            width_mm=100,
            depth_mm=100,
            height_u=42,
            position_x=1,
            position_y=1,
        )
        session.add(rack)
        session.commit()
        rack_id, version = str(rack.id), rack.version
    payload = {
        "expected_version": version,
        "position_x": 0.1,
        "position_y": 1,
        "position_z": 0,
        "rotation": 0,
    }
    outside = editor.patch(f"/api/v1/scene/racks/{rack_id}", headers=headers(world), json=payload)
    assert outside.status_code == 422
    accepted = editor.patch(
        f"/api/v1/scene/racks/{rack_id}",
        headers=headers(world),
        json={**payload, "position_x": 0.25, "position_y": 0.225},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["width_mm"] == 100 and accepted.json()["depth_mm"] == 100
    assert accepted.json()["version"] == version + 1

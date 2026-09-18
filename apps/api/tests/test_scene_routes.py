from __future__ import annotations

import json
import csv
import io
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

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
    Location,
    Pathway as PathwayModel,
    PathwaySegment,
    Port,
    TenantMembership,
)


@pytest.fixture
def routing(world, monkeypatch):
    monkeypatch.setattr(deps, "SessionLocal", world.session_factory)
    # Each isolated database fixture also needs its own ingress quota.
    client = TestClient(app, client=(f"route-test-{world.tenant_a}", 50000))
    headers = {
        "X-Tenant-ID": str(world.tenant_a),
        "X-Actor-ID": str(world.admin),
        "X-Project-ID": str(world.project),
        "X-Location-ID": str(world.tr),
    }

    def post(path, body):
        response = client.post("/api/v1/scene" + path, headers=headers, json=body)
        assert response.status_code == 201, response.text
        return response.json()

    room = post(
        "/rooms",
        {
            "parent_id": str(world.tr),
            "identifier": "ROUTE-ROOM",
            "name": "Routing room",
            "kind": "server_room",
            "width_m": 10,
            "depth_m": 8,
            "height_m": 3.6,
        },
    )
    racks = post(
        "/racks",
        {
            "location_id": room["id"],
            "identifier_prefix": "RR",
            "name_prefix": "Routing rack",
            "count": 2,
            "columns": 2,
            "position_x": 1,
            "position_y": 1,
            "gap_m": 3.4,
        },
    )["racks"]
    devices = [
        post(
            "/devices",
            {
                "rack_id": rack["id"],
                "template_id": str(world.switch_template),
                "identifier": f"ROUTE-D{i}",
                "name": f"Device {i}",
                "start_u": 1,
            },
        )
        for i, rack in enumerate(racks)
    ]
    with world.scoped_session() as session:
        ports = [
            str(
                session.scalar(
                    select(Port.id)
                    .where(Port.device_id == uuid.UUID(device["id"]))
                    .order_by(Port.position_index)
                    .limit(1)
                )
            )
            for device in devices
        ]

    def tray(identifier, points):
        value = post(
            "/pathways",
            {
                "location_id": room["id"],
                "identifier": identifier,
                "name": identifier,
                "pathway_type": "basket_tray",
                "segments": [{"name": identifier, "coordinates": points}],
            },
        )
        with world.scoped_session() as session:
            segment = session.scalar(
                select(PathwaySegment).where(PathwaySegment.pathway_id == uuid.UUID(value["id"]))
            )
            value["segment_id"] = str(segment.id)
        return value

    return SimpleNamespace(
        client=client,
        headers=headers,
        room=room,
        racks=racks,
        devices=devices,
        ports=ports,
        post=post,
        tray=tray,
    )


def point(x, y=1, z=2.5):
    return {"x": x, "y": y, "z": z}


def preview(routing, **changes):
    return routing.client.post(
        "/api/v1/scene/routes/preview",
        headers=routing.headers,
        json={
            "port_a_id": routing.ports[0],
            "port_b_id": routing.ports[1],
            "media_type": "Cat6A copper",
            **changes,
        },
    )


def cable_body(routing, **changes):
    return {
        "identifier": "ROUTE-CABLE",
        "port_a_id": routing.ports[0],
        "port_b_id": routing.ports[1],
        "media_type": "Cat6A copper",
        "construction": "patch_cord",
        **changes,
    }


def test_preview_returns_continuous_alternatives_and_excludes_trays_without_writes(world, routing):
    start = routing.tray("START", [point(1), point(2)])
    middle = routing.tray("MIDDLE", [point(2), point(4)])
    end = routing.tray("END", [point(4), point(5)])
    alternate = routing.tray("ALTERNATE", [point(2), point(2, 3), point(4, 3), point(4)])
    with world.scoped_session() as session:
        before = session.scalar(select(func.count(AuditEvent.id)))
    response = preview(routing)
    assert response.status_code == 200, response.text
    candidates = response.json()["candidates"]
    assert 2 <= len(candidates) <= 3
    expected = [start["segment_id"], middle["segment_id"], end["segment_id"]]
    assert candidates[0]["segment_ids"] == expected
    assert candidates[0]["length_m"] > sum(
        segment["length_m"] for segment in candidates[0]["segments"]
    )
    assert candidates[0]["warnings"]
    excluded = preview(routing, excluded_pathway_ids=[middle["id"]]).json()["candidates"]
    assert excluded and all(middle["segment_id"] not in item["segment_ids"] for item in excluded)
    assert any(alternate["segment_id"] in item["segment_ids"] for item in excluded)
    with world.scoped_session() as session:
        assert session.scalar(select(func.count(AuditEvent.id))) == before
        assert (
            session.scalar(
                select(PhysicalPortClaim.id).where(
                    PhysicalPortClaim.port_id.in_([uuid.UUID(x) for x in routing.ports])
                )
            )
            is None
        )


def test_custom_disconnected_and_reordered_routes_are_rejected_at_preview_and_save(world, routing):
    first = routing.tray("FIRST", [point(1), point(2)])
    middle = routing.tray("MIDDLE", [point(2), point(4)])
    last = routing.tray("LAST", [point(4), point(5)])
    for ids in (
        [first["segment_id"], last["segment_id"]],
        [first["segment_id"], last["segment_id"], middle["segment_id"]],
    ):
        assert preview(routing, route_segment_ids=ids).status_code == 422
        response = routing.client.post(
            "/api/v1/scene/cables",
            headers=routing.headers,
            json=cable_body(routing, route_segment_ids=ids),
        )
        assert response.status_code == 422
    valid = [first["segment_id"], middle["segment_id"], last["segment_id"]]
    checked = preview(routing, route_segment_ids=valid)
    assert checked.status_code == 200
    assert checked.json()["candidates"][0]["segment_ids"] == valid
    created = routing.client.post(
        "/api/v1/scene/cables",
        headers=routing.headers,
        json=cable_body(routing, route_segment_ids=valid),
    )
    assert created.status_code == 201, created.text
    assert preview(routing).status_code == 409


def test_disconnected_automatic_routes_do_not_invent_a_gap(routing):
    routing.tray("LEFT", [point(1), point(2)])
    routing.tray("RIGHT", [point(4), point(5)])
    response = preview(routing)
    assert response.status_code == 200
    assert response.json()["candidates"] == []
    assert response.json()["warnings"]
    direct = preview(routing, route_segment_ids=[]).json()["candidates"][0]
    assert direct["id"] == "direct" and direct["segments"] == [] and direct["length_m"] > 0
    assert direct["warnings"]


@pytest.mark.parametrize("kind", ["device", "pathway"])
def test_policy_versions_and_scene_and_canonical_creates_cannot_bypass_restrictions(
    world, routing, kind
):
    pathway = routing.tray("POLICY-TRAY", [point(1), point(5)])
    object_id = routing.devices[0]["id"] if kind == "device" else pathway["id"]
    policy = {"expected_version": 1, "allows_cables": False, "allowed_media": ["copper", "fiber"]}
    path = f"/api/v1/scene/{kind}s/{object_id}/cable-policy"
    updated = routing.client.patch(path, headers=routing.headers, json=policy)
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert routing.client.patch(path, headers=routing.headers, json=policy).status_code == 409
    for endpoint in ("/api/v1/scene/cables", "/api/v1/cables"):
        denied = routing.client.post(
            endpoint,
            headers=routing.headers,
            json=cable_body(routing, route_segment_ids=[pathway["segment_id"]]),
        )
        assert denied.status_code == 422, denied.text
    custom = preview(routing, route_segment_ids=[pathway["segment_id"]])
    assert custom.status_code == 422
    restore = {"expected_version": 2, "allows_cables": True, "allowed_media": ["fiber"]}
    assert routing.client.patch(path, headers=routing.headers, json=restore).status_code == 200
    assert preview(routing, route_segment_ids=[pathway["segment_id"]]).status_code == 422
    scene = routing.client.get("/api/v1/scene", headers=routing.headers).json()
    row = next(item for item in scene[f"{kind}s"] if item["id"] == object_id)
    assert row["cable_policy"] == {"allows_cables": True, "allowed_media": ["fiber"]}
    assert row["version"] == 3
    with world.scoped_session() as session:
        assert session.scalar(select(Cable.id).where(Cable.identifier == "ROUTE-CABLE")) is None


def test_policy_changed_after_preview_is_revalidated_at_save(routing):
    pathway = routing.tray("POLICY-TRAY", [point(1), point(5)])
    candidate = preview(routing).json()["candidates"][0]
    assert candidate["segment_ids"] == [pathway["segment_id"]]
    response = routing.client.patch(
        f"/api/v1/scene/pathways/{pathway['id']}/cable-policy",
        headers=routing.headers,
        json={"expected_version": 1, "allows_cables": False, "allowed_media": []},
    )
    assert response.status_code == 200
    denied = routing.client.post(
        "/api/v1/scene/cables",
        headers=routing.headers,
        json=cable_body(routing, route_segment_ids=candidate["segment_ids"]),
    )
    assert denied.status_code == 422


def test_entrances_persist_require_one_and_validate_wall_overlap_and_version(world, routing):
    initial = routing.room["dimensions"]["entrances"]
    assert len(initial) == 1 and initial[0]["wall"] == "south" and initial[0]["offset_m"] == 4.5
    entrances = [
        {
            "id": "north-door",
            "name": "North entrance",
            "wall": "north",
            "offset_m": 1,
            "width_m": 1.2,
            "height_m": 2.2,
        },
        {
            "id": "east-door",
            "name": "East entrance",
            "wall": "east",
            "offset_m": 2,
            "width_m": 1,
            "height_m": 2.1,
        },
    ]
    path = f"/api/v1/scene/rooms/{routing.room['id']}/entrances"
    updated = routing.client.patch(
        path, headers=routing.headers, json={"expected_version": 1, "entrances": entrances}
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert (
        routing.client.patch(
            path, headers=routing.headers, json={"expected_version": 1, "entrances": entrances}
        ).status_code
        == 409
    )
    for invalid in (
        [],
        [{**entrances[0], "offset_m": 9}],
        [entrances[0], {**entrances[0], "id": "overlap", "offset_m": 1.5}],
        [{**entrances[0], "height_m": 10}],
    ):
        assert (
            routing.client.patch(
                path, headers=routing.headers, json={"expected_version": 2, "entrances": invalid}
            ).status_code
            == 422
        )
    with world.scoped_session() as session:
        room = session.get(Location, uuid.UUID(routing.room["id"]))
        assert room.version == 2 and room.dimensions["entrances"] == entrances
        assert session.scalar(
            select(AuditEvent.id).where(AuditEvent.action == "location.entrances_updated")
        )


def test_route_and_policy_cannot_reference_hidden_locations(world, routing):
    with world.scoped_session() as session:
        hidden = PathwayModel(
            tenant_id=world.tenant_a,
            location_id=world.other_building,
            identifier="HIDDEN-ROUTE",
            name="Hidden tray",
            pathway_type="tray",
        )
        session.add(hidden)
        session.flush()
        segment = PathwaySegment(
            tenant_id=world.tenant_a,
            pathway_id=hidden.id,
            sequence=1,
            name="Hidden segment",
            coordinates=[point(1), point(5)],
        )
        session.add(segment)
        session.commit()
        hidden_id, segment_id = str(hidden.id), str(segment.id)
    assert preview(routing, excluded_pathway_ids=[hidden_id]).status_code == 403
    assert preview(routing, route_segment_ids=[segment_id]).status_code == 403
    response = routing.client.patch(
        f"/api/v1/scene/pathways/{hidden_id}/cable-policy",
        headers=routing.headers,
        json={"expected_version": 1, "allows_cables": False, "allowed_media": []},
    )
    assert response.status_code == 403


def test_preview_does_not_guess_cross_room_coordinates(world, routing):
    with world.scoped_session() as session:
        device = session.get(Device, uuid.UUID(routing.devices[1]["id"]))
        # A second room in the authorized TR subtree, with no shared coordinate frame.
        other = Location(
            tenant_id=world.tenant_a,
            parent_id=world.tr,
            location_type="room",
            identifier="OTHER-ROOM",
            name="Other room",
            dimensions={"width_m": 10, "depth_m": 8, "height_m": 3.6},
        )
        from app.models import LocationType, Rack

        other.location_type = LocationType.ROOM
        session.add(other)
        session.flush()
        session.get(Rack, device.rack_id).location_id = other.id
        device.location_id = other.id
        session.commit()
    result = preview(routing)
    assert result.status_code == 200 and result.json()["candidates"] == []
    assert result.json()["warnings"]


def test_policy_and_entrance_updates_need_explicit_write_permissions(world, routing):
    with world.scoped_session() as session:
        member = session.scalar(
            select(TenantMembership).where(TenantMembership.user_id == world.admin)
        )
        member.permissions = [
            f"{resource}:read"
            for resource in ("location", "rack", "device", "port", "pathway", "cable")
        ] + ["cable:create"]
        session.commit()
    assert preview(routing).status_code == 200
    response = routing.client.patch(
        f"/api/v1/scene/devices/{routing.devices[0]['id']}/cable-policy",
        headers=routing.headers,
        json={"expected_version": 1, "allows_cables": False, "allowed_media": []},
    )
    assert response.status_code == 403
    response = routing.client.patch(
        f"/api/v1/scene/rooms/{routing.room['id']}/entrances",
        headers=routing.headers,
        json={"expected_version": 1, "entrances": routing.room["dimensions"]["entrances"]},
    )
    assert response.status_code == 403


def test_migration_009_backfills_existing_pathways_and_round_trips(tmp_path):
    root = Path(__file__).resolve().parents[3]
    database = tmp_path / "policy.db"
    url = f"sqlite+pysqlite:///{database}"
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "PLATFORM_DATABASE_URL": url,
        "MIGRATION_DATABASE_URL": url,
    }

    def migrate(command, revision):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", command, revision],
            cwd=root / "apps/api",
            env=env,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("upgrade", "200000000008")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO pathways(id,tenant_id,location_id,identifier,name,pathway_type,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                uuid.uuid4().hex,
                uuid.uuid4().hex,
                "LEGACY",
                "Legacy tray",
                "tray",
                "active",
                1,
                "2026-01-01",
                "2026-01-01",
            ),
        )
    migrate("upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert json.loads(
            connection.execute("SELECT cable_policy FROM pathways").fetchone()[0]
        ) == {"allows_cables": True, "allowed_media": ["copper", "fiber"]}
    migrate("downgrade", "200000000008")
    migrate("upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert (
            json.loads(connection.execute("SELECT cable_policy FROM pathways").fetchone()[0])[
                "allows_cables"
            ]
            is True
        )


@pytest.mark.parametrize("gap,expected_status", [(0.0005, 200), (0.002, 422)])
def test_route_join_uses_one_millimetre_tolerance(routing, gap, expected_status):
    left = routing.tray("LEFT-TOLERANCE", [point(1), point(3)])
    right = routing.tray("RIGHT-TOLERANCE", [point(3 + gap), point(5)])
    result = preview(routing, route_segment_ids=[left["segment_id"], right["segment_id"]])
    assert result.status_code == expected_status, result.text
    automatic = preview(routing)
    assert automatic.status_code == 200
    assert bool(automatic.json()["candidates"]) is (expected_status == 200)


def test_preview_stops_at_bounded_inventory_and_explains_how_to_narrow(routing, monkeypatch):
    from app.services.scene_routing import SceneRouteService

    routing.tray("CAP-LEFT", [point(1), point(3)])
    routing.tray("CAP-RIGHT", [point(3), point(5)])
    monkeypatch.setattr(SceneRouteService, "MAX_SEGMENTS", 1)
    response = preview(routing)
    assert response.status_code == 200
    assert response.json()["candidates"] == [] and response.json()["warnings"]


def test_partial_tray_projection_roundtrips_with_audit_and_proportional_length(world, routing):
    tray = routing.tray("PARTIAL-L", [point(0), point(8), point(8, 7)])
    with world.scoped_session() as db:
        db.get(PathwaySegment, uuid.UUID(tray["segment_id"])).length_m = 28.0
        db.commit()
    candidate = preview(routing).json()["candidates"][0]
    part = candidate["route_portions"][0]
    assert 0 < part["start_offset_m"] < part["end_offset_m"] < 8
    assert part["end_offset_m"] - part["start_offset_m"] == pytest.approx(4.0)
    assert candidate["segments"][0]["length_m"] == pytest.approx(8.0)
    assert candidate["segments"][0]["full_length_m"] == 28
    assert candidate["length_m"] < 14
    reversed_candidate = preview(
        routing, port_a_id=routing.ports[1], port_b_id=routing.ports[0]
    ).json()["candidates"][0]
    assert reversed_candidate["route_portions"][0]["start_offset_m"] == part["end_offset_m"]
    assert reversed_candidate["route_portions"][0]["end_offset_m"] == part["start_offset_m"]
    response = routing.client.post(
        "/api/v1/scene/cables",
        headers=routing.headers,
        json=cable_body(
            routing,
            route_segment_ids=candidate["segment_ids"],
            route_portions=candidate["route_portions"],
            length_m=candidate["length_m"] + 2,
        ),
    )
    assert response.status_code == 201, response.text
    created_id = response.json()["id"]
    data = routing.client.get("/api/v1/scene", headers=routing.headers).json()
    saved = next(c for c in data["cables"] if c["id"] == created_id)
    assert saved["route_portions"] == [{**part, "valid": True}]
    assert saved["route_scope"] == "complete"

    def trace_and_report():
        trace = routing.client.get(f"/api/v1/cables/{created_id}/trace", headers=routing.headers)
        assert trace.status_code == 200, trace.text
        route = next(i for i in trace.json()["items"] if i.get("id") == created_id)["route"][0]
        report = routing.client.get("/api/v1/reports/cable-schedule.csv", headers=routing.headers)
        assert report.status_code == 200, report.text
        row = next(
            row
            for row in csv.DictReader(io.StringIO(report.content.decode("utf-8-sig")))
            if row["Cable ID"] == created_id
        )
        return route, row

    trace, report = trace_and_report()
    assert trace["valid"] and trace["length_m"] == pytest.approx(8)
    assert trace["full_length_m"] == 28 and len(trace["coordinates"]) == 2
    assert trace["coordinates"][0]["x"] == pytest.approx(part["start_offset_m"])
    assert float(report["Route Length (m)"]) == 8
    with world.scoped_session() as db:
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == uuid.UUID(created_id), AuditEvent.action == "cable.created"
            )
        )
        assert event.after["route_portions"] == candidate["route_portions"]
        segment = db.get(PathwaySegment, uuid.UUID(tray["segment_id"]))
        assert segment.length_m == 28 and segment.coordinates == [point(0), point(8), point(8, 7)]
        segment.coordinates = [point(0), point(7), point(7, 7)]
        db.commit()
    changed = routing.client.get("/api/v1/scene", headers=routing.headers).json()
    assert (
        next(c for c in changed["cables"] if c["id"] == created_id)["route_portions"][0]["valid"]
        is False
    )
    trace, report = trace_and_report()
    assert trace["valid"] is False and trace["length_m"] is None and trace["coordinates"] == []
    assert report["Route Length (m)"] == ""
    assert "geometry changed" in report["Pathway Route"]


@pytest.mark.parametrize("endpoint", ["/api/v1/scene/cables", "/api/v1/cables"])
@pytest.mark.parametrize(
    "change,status", [("range", 422), ("wrong_id", 422), ("stale", 409), ("missing", 422)]
)
def test_both_creation_paths_reject_invalid_or_stale_portions_without_writes(
    world, routing, endpoint, change, status
):
    tray = routing.tray("PARTIAL-CHECK", [point(0), point(8)])
    candidate = preview(routing).json()["candidates"][0]
    parts = candidate["route_portions"]
    if change == "range":
        parts[0]["end_offset_m"] = 100
    elif change == "wrong_id":
        parts[0]["segment_id"] = str(uuid.uuid4())
    elif change == "missing":
        parts = []
    else:
        with world.scoped_session() as db:
            db.get(PathwaySegment, uuid.UUID(tray["segment_id"])).length_m = 9.0
            db.commit()
    response = routing.client.post(
        endpoint,
        headers=routing.headers,
        json=cable_body(routing, route_segment_ids=candidate["segment_ids"], route_portions=parts),
    )
    assert response.status_code == status, response.text
    with world.scoped_session() as db:
        assert db.scalar(select(Cable.id).where(Cable.identifier == "ROUTE-CABLE")) is None
        assert (
            db.scalar(
                select(PhysicalPortClaim.id).where(
                    PhysicalPortClaim.port_id.in_([uuid.UUID(id) for id in routing.ports])
                )
            )
            is None
        )


def test_partial_multi_segment_route_uses_full_middle_and_rejects_interior_junction(world, routing):
    first = routing.tray("PART-START", [point(0), point(2)])
    middle = routing.tray("PART-MID", [point(2), point(4)])
    last = routing.tray("PART-END", [point(4), point(8)])
    candidate = preview(
        routing, route_segment_ids=[r["segment_id"] for r in (first, middle, last)]
    ).json()["candidates"][0]
    portions = candidate["route_portions"]
    assert portions[0]["end_offset_m"] == 2
    assert (portions[1]["start_offset_m"], portions[1]["end_offset_m"]) == (0, 2)
    assert portions[2]["start_offset_m"] == 0 and portions[2]["end_offset_m"] < 4
    portions[1]["start_offset_m"] = 0.5
    response = routing.client.post(
        "/api/v1/cables",
        headers=routing.headers,
        json=cable_body(
            routing, route_segment_ids=candidate["segment_ids"], route_portions=portions
        ),
    )
    assert response.status_code == 422

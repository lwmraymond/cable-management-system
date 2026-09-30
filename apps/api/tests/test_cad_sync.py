import io
import uuid

import pytest
from sqlalchemy import func, select

pytest.importorskip("ezdxf")
pytest.importorskip("ifcopenshell")
import ezdxf

from app.cad_models import CadApplication, CadImportRevision, CadSnapshot
from app.models import AuditEvent, CableRouteSegment, PathwaySegment, Rack, TenantMembership
from app.services.scene_editor import SceneEditorService
from test_cad_formats import edited, dxf_bytes
from test_scene_editor import editor as editor_fixture, room as room_fixture, headers, create_racks

editor = editor_fixture
room = room_fixture


@pytest.fixture
def exported(world, editor, room):
    racks = create_racks(world, editor, room, count=2, columns=2)
    return racks


def export(world, client, room, format="dxf"):
    response = client.post(
        "/api/v1/scene/cad/exports",
        headers=headers(world),
        json={"location_id": room["id"], "format": format},
    )
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    response = client.get(f"/api/v1/scene/cad/exports/{identifier}/file", headers=headers(world))
    assert response.status_code == 200, response.text
    return identifier, response.content


def stage(world, client, room, raw, format="dxf", name=None):
    response = client.post(
        f"/api/v1/scene/cad/imports?location_id={room['id']}",
        headers=headers(world),
        files={"file": (name or f"exchange.{format}", raw, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def apply(world, client, preview):
    return client.post(
        f"/api/v1/scene/cad/imports/{preview['id']}/apply",
        headers=headers(world),
        json={"preview_token": preview.get("preview_token", "0" * 64)},
    )


def counts(world):
    with world.scoped_session() as db:
        return {
            m.__tablename__: db.scalar(select(func.count()).select_from(m))
            for m in (Rack, CadSnapshot, CadImportRevision, CadApplication, AuditEvent)
        }


@pytest.mark.parametrize("format", ["dxf", "ifc"])
def test_native_geometry_preview_apply_reload_duplicate(world, editor, room, exported, format):
    snapshot, raw = export(world, editor, room, format)
    rack = exported[0]
    changed = edited(raw, format, rack["id"], [3.0, 4.0, 0.2], 90)
    preview = stage(world, editor, room, changed, format)
    assert preview["can_apply"], preview
    assert [d["id"] for d in preview["diffs"] if d["status"] == "update"] == [rack["id"]]
    with world.scoped_session() as db:
        assert db.get(Rack, uuid.UUID(rack["id"])).position_x == 1  # staging is not apply
    saved = apply(world, editor, preview)
    assert saved.status_code == 200, saved.text
    with world.scoped_session() as db:
        row = db.get(Rack, uuid.UUID(rack["id"]))
        assert [row.position_x, row.position_y, row.position_z, row.rotation, row.version] == [
            3,
            4,
            0.2,
            90,
            rack["version"] + 1,
        ]
    before = counts(world)
    assert apply(world, editor, preview).json() == saved.json()
    repeated = stage(world, editor, room, changed, format, name=f"renamed.{format}")
    assert repeated["id"] == preview["id"] and repeated["applied"]
    assert counts(world) == before
    original = editor.get(f"/api/v1/scene/cad/imports/{preview['id']}/file", headers=headers(world))
    assert original.content == changed
    assert (
        editor.get(f"/api/v1/scene/cad/exports/{snapshot}/file", headers=headers(world)).content
        == raw
    )


def test_stale_preview_and_three_way_conflict_no_overwrite(world, editor, room, exported):
    _, raw = export(world, editor, room)
    rack = exported[0]
    preview = stage(world, editor, room, edited(raw, "dxf", rack["id"], [3, 4, 0]))
    moved = editor.patch(
        f"/api/v1/scene/racks/{rack['id']}",
        headers=headers(world),
        json={"expected_version": rack["version"], "position_x": 5, "position_y": 4, "rotation": 0},
    )
    assert moved.status_code == 200, moved.text
    before = counts(world)
    assert apply(world, editor, preview).status_code == 409
    new = editor.get(f"/api/v1/scene/cad/imports/{preview['id']}", headers=headers(world)).json()
    assert any(d["status"] == "conflict" for d in new["diffs"])
    assert counts(world) == before


def test_whole_batch_rolls_back_prior_geometry_audit_version_and_receipt(
    world, editor, room, exported, monkeypatch
):
    _, raw = export(world, editor, room)
    raw = edited(
        edited(raw, "dxf", exported[0]["id"], [3, 4, 0]), "dxf", exported[1]["id"], [6, 4, 0]
    )
    preview = stage(world, editor, room, raw)
    before = counts(world)
    real = SceneEditorService.move_rack
    calls = []

    def injected(self, *args):
        result = real(self, *args)
        calls.append(result.id)
        if len(calls) == 2:
            from app.exceptions import ConflictError

            raise ConflictError("synthetic late failure")
        return result

    monkeypatch.setattr(SceneEditorService, "move_rack", injected)
    assert apply(world, editor, preview).status_code == 409
    assert len(calls) == 2
    assert counts(world) == before
    with world.scoped_session() as db:
        for rack in exported:
            row = db.get(Rack, uuid.UUID(rack["id"]))
            assert [row.position_x, row.position_y, row.version] == [
                rack["position_x"],
                rack["position_y"],
                rack["version"],
            ]


@pytest.mark.parametrize(
    "mutation", ["missing", "unknown", "duplicate", "metadata", "shape", "invalid"]
)
def test_invalid_identity_partial_and_shape_are_never_applied(
    world, editor, room, exported, mutation
):
    _, raw = export(world, editor, room)
    doc = ezdxf.read(io.StringIO(raw.decode()))
    entity = next(e for e in doc.modelspace() if e.get_xdata("CMS_CAD")[1].value == "rack")
    if mutation == "missing":
        doc.modelspace().delete_entity(entity)
    elif mutation == "duplicate":
        doc.modelspace().add_entity(entity.copy())
    elif mutation in {"unknown", "metadata"}:
        tags = list(entity.get_xdata("CMS_CAD"))
        tags[0 if mutation == "unknown" else 3] = (
            (1000, str(uuid.uuid4())) if mutation == "unknown" else (1071, 99)
        )
        entity.set_xdata("CMS_CAD", tags)
    elif mutation == "shape":
        for line in doc.blocks[entity.dxf.name]:
            line.dxf.start = (line.dxf.start.x * 2, line.dxf.start.y, line.dxf.start.z)
            line.dxf.end = (line.dxf.end.x * 2, line.dxf.end.y, line.dxf.end.z)
    changed = b"invalid CAD" if mutation == "invalid" else dxf_bytes(doc)
    before = counts(world)
    preview = stage(world, editor, room, changed)
    assert not preview["can_apply"], preview
    assert apply(world, editor, preview).status_code == 409
    after = counts(world)
    assert (
        after["racks"] == before["racks"]
        and after["cad_applications"] == before["cad_applications"]
    )


def test_permissions_workspace_and_tenant_isolation(world, editor, room, exported):
    snapshot, raw = export(world, editor, room)
    preview = stage(world, editor, room, edited(raw, "dxf", exported[0]["id"], [3, 4, 0]))
    with world.scoped_session() as db:
        member = db.scalar(
            select(TenantMembership).where(TenantMembership.user_id == world.supervisor)
        )
        member.permissions = [
            "location:read",
            "rack:read",
            "device:read",
            "port:read",
            "pathway:read",
            "cable:read",
            "report:export",
        ]
        db.commit()
    denied = editor.post(
        f"/api/v1/scene/cad/imports/{preview['id']}/apply",
        headers=headers(world, actor=world.supervisor),
        json={"preview_token": preview["preview_token"]},
    )
    assert denied.status_code == 403
    other = {"X-Tenant-ID": str(world.tenant_b), "X-Actor-ID": str(world.admin)}
    assert editor.get(f"/api/v1/scene/cad/exports/{snapshot}/file", headers=other).status_code in {
        403,
        404,
    }
    personal = {"X-Tenant-ID": str(world.tenant_a), "X-Actor-ID": str(world.admin)}
    assert (
        editor.get(f"/api/v1/scene/cad/exports/{snapshot}/file", headers=personal).status_code
        == 403
    )
    assert counts(world)["cad_applications"] == 0


def test_immutable_history_and_noop_does_not_change_versions(world, editor, room, exported):
    snapshot, raw = export(world, editor, room)
    preview = stage(world, editor, room, raw)
    assert preview["can_apply"] and all(d["status"] == "unchanged" for d in preview["diffs"])
    assert apply(world, editor, preview).status_code == 200
    with world.scoped_session() as db:
        assert db.get(Rack, uuid.UUID(exported[0]["id"])).version == exported[0]["version"]
        row = db.get(CadSnapshot, uuid.UUID(snapshot))
        row.sha256 = "changed"
        with pytest.raises(PermissionError):
            db.commit()
        db.rollback()


def test_unused_tray_update_preserves_length_and_claim_race_is_blocked(
    world, editor, room, exported
):
    from test_scene_editor import pathway_payload

    response = editor.post(
        "/api/v1/scene/pathways", headers=headers(world), json=pathway_payload(room)
    )
    assert response.status_code == 201
    _, raw = export(world, editor, room)
    doc = ezdxf.read(io.StringIO(raw.decode()))
    entity = next(e for e in doc.modelspace() if e.dxftype() == "POLYLINE")
    segment_id = uuid.UUID(entity.get_xdata("CMS_CAD")[0].value)
    entity.vertices[1].dxf.location = (5, 2, 2)
    preview = stage(world, editor, room, dxf_bytes(doc))
    assert preview["can_apply"]
    assert apply(world, editor, preview).status_code == 200
    with world.scoped_session() as db:
        segment = db.get(PathwaySegment, segment_id)
        assert segment.coordinates[-1] == {"x": 5, "y": 2, "z": 2}
        assert segment.length_m == 4
    # Another cable claim after staging must invalidate Apply, including its receipt.
    _, raw = export(world, editor, room)
    doc = ezdxf.read(io.StringIO(raw.decode()))
    entity = next(e for e in doc.modelspace() if e.dxftype() == "POLYLINE")
    entity.vertices[1].dxf.location = (6, 2, 2)
    preview = stage(world, editor, room, dxf_bytes(doc))
    assert preview["can_apply"]
    with world.scoped_session() as db:
        db.add(
            CableRouteSegment(
                tenant_id=world.tenant_a,
                cable_id=world.patch_cord,
                pathway_segment_id=segment_id,
                sequence=99,
            )
        )
        db.commit()
    before = counts(world)
    assert apply(world, editor, preview).status_code == 409
    refreshed = editor.get(
        f"/api/v1/scene/cad/imports/{preview['id']}", headers=headers(world)
    ).json()
    assert any(
        d["status"] == "blocked" and "offsets/hash" in d["reason"] for d in refreshed["diffs"]
    )
    assert counts(world) == before


def test_concurrent_duplicate_apply_has_one_business_commit(
    world, editor, room, exported, tmp_path, monkeypatch
):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from app.api import deps

    _, raw = export(world, editor, room)
    preview = stage(world, editor, room, edited(raw, "dxf", exported[0]["id"], [3, 4, 0]))
    # Separate DB connections are essential: StaticPool is not a concurrency proof.
    path = tmp_path / "concurrent.sqlite3"
    connection = world.session_factory.kw["bind"].raw_connection()
    target = sqlite3.connect(path)
    connection.driver_connection.backup(target)
    target.close()
    connection.close()
    engine = create_engine(
        "sqlite+pysqlite:///" + str(path), connect_args={"check_same_thread": False, "timeout": 10}
    )
    factory = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(deps, "SessionLocal", factory)
    isolated = replace(world, session_factory=factory)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: apply(isolated, editor, preview), range(2)))
        assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
        assert results[0].json() == results[1].json()
        assert counts(isolated)["cad_applications"] == 1
        with isolated.scoped_session() as db:
            assert db.get(Rack, uuid.UUID(exported[0]["id"])).version == exported[0]["version"] + 1
    finally:
        engine.dispose()


def test_simultaneous_swap_is_explicitly_blocked_in_preview(world, editor, room, exported):
    _, raw = export(world, editor, room)
    a, b = exported
    raw = edited(
        edited(raw, "dxf", a["id"], [b["position_x"], b["position_y"], b["position_z"]]),
        "dxf",
        b["id"],
        [a["position_x"], a["position_y"], a["position_z"]],
    )
    preview = stage(world, editor, room, raw)
    assert not preview["can_apply"]
    assert any("v1" in d["reason"] and d["status"] == "blocked" for d in preview["diffs"])
    assert apply(world, editor, preview).status_code == 409


@pytest.mark.parametrize("mutation", ["name", "containment", "new_connection"])
def test_ifc_readonly_semantics_are_explicitly_blocked(world, editor, room, exported, mutation):
    import ifcopenshell

    _, raw = export(world, editor, room, "ifc")
    file = ifcopenshell.file.from_string(raw.decode())
    if mutation == "name":
        file.by_type("IfcBuildingElementProxy")[0].Name = "Changed identity"
    elif mutation == "containment":
        file.remove(file.by_type("IfcRelContainedInSpatialStructure")[0])
    else:
        ports = [
            file.create_entity("IfcDistributionPort", GlobalId=ifcopenshell.guid.new())
            for _ in range(2)
        ]
        file.create_entity(
            "IfcRelConnectsPorts",
            GlobalId=ifcopenshell.guid.new(),
            RelatingPort=ports[0],
            RelatedPort=ports[1],
        )
    preview = stage(world, editor, room, file.to_string().encode(), "ifc")
    assert not preview["can_apply"] and "连接关系" in preview["error"]
    assert apply(world, editor, preview).status_code == 409


@pytest.mark.parametrize("format", ["dxf", "ifc"])
def test_tray_outside_room_is_blocked_before_apply(world, editor, room, exported, format):
    import ifcopenshell
    from test_scene_editor import pathway_payload

    editor.post("/api/v1/scene/pathways", headers=headers(world), json=pathway_payload(room))
    _, raw = export(world, editor, room, format)
    if format == "dxf":
        doc = ezdxf.read(io.StringIO(raw.decode()))
        next(e for e in doc.modelspace() if e.dxftype() == "POLYLINE").vertices[1].dxf.location = (
            -1,
            2,
            2,
        )
        raw = dxf_bytes(doc)
    else:
        file = ifcopenshell.file.from_string(raw.decode())
        file.by_type("IfcCableCarrierSegment")[0].Representation.Representations[0].Items[0].Points[
            1
        ].Coordinates = (-1.0, 2.0, 2.0)
        raw = file.to_string().encode()
    preview = stage(world, editor, room, raw, format)
    assert not preview["can_apply"]
    assert any(d["status"] == "blocked" and "边界" in d["reason"] for d in preview["diffs"])
    before = counts(world)
    assert apply(world, editor, preview).status_code == 409
    assert counts(world) == before


def test_export_change_during_conversion_rejects_torn_snapshot(
    world, editor, room, exported, monkeypatch
):
    import app.services.cad_sync as sync

    real = sync.export_file
    before = counts(world)

    def changed(format, document):
        with world.scoped_session() as db:
            rack = db.get(Rack, uuid.UUID(exported[0]["id"]))
            rack.position_x = 3
            db.commit()
        return real(format, document)

    monkeypatch.setattr(sync, "export_file", changed)
    response = editor.post(
        "/api/v1/scene/cad/exports",
        headers=headers(world),
        json={"location_id": room["id"], "format": "dxf"},
    )
    assert response.status_code == 409, response.text
    after = counts(world)
    assert after["cad_snapshots"] == before["cad_snapshots"]
    assert after["audit_events"] == before["audit_events"]

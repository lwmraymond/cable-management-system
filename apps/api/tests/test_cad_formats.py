import copy
import io
import math
import uuid

import pytest

pytest.importorskip("ezdxf")
pytest.importorskip("ifcopenshell")
import ezdxf
import ifcopenshell
from ifcopenshell.util.element import get_psets

from app.exceptions import ValidationError
from app.services.cad_dxf import parse_dxf
from app.services.cad_formats import export_file, parse_file
from app.services.cad_geometry import digest
from app.services.cad_ifc import parse_ifc


@pytest.fixture
def exchange():
    ids = {
        kind: str(uuid.uuid4())
        for kind in ("room", "rack", "device", "front", "rear", "tray", "cable")
    }
    objects = []

    def add(key, kind, geometry, **attributes):
        objects.append(
            {
                "id": ids[key],
                "kind": kind,
                "version": 2,
                "identifier": key,
                "geometry": geometry,
                "attributes": attributes,
            }
        )

    add("room", "room", {"position": [0, 0, 0], "rotation": 0, "size": [10, 8, 4]})
    add("rack", "rack", {"position": [2, 3, 0.3], "rotation": 90, "size": [0.6, 1, 2]})
    add(
        "device",
        "device",
        {"position": [2, 3, 0.6], "rotation": 90, "size": [0.48, 0.35, 0.041]},
        rack_id=ids["rack"],
    )
    add("front", "port", {"position": [2.5, 3, 0.65]}, device_id=ids["device"], face="front")
    add("rear", "port", {"position": [1.5, 3, 0.65]}, device_id=ids["device"], face="rear")
    add(
        "tray",
        "pathway_segment",
        {"points": [[1, 2, 0.5], [4, 6, 0.5], [4, 6, 2.5]], "length_m": 7},
    )
    add(
        "cable",
        "cable",
        {"paths": [[[1, 2, 0.5], [4, 6, 0.5], [4, 6, 2.5]]]},
        port_ids=[ids["front"], ids["rear"]],
    )
    return {
        "profile": "CMS-CAD-1",
        "snapshot_id": str(uuid.uuid4()),
        "location_id": ids["room"],
        "objects": objects,
        "port_mappings": [[ids["front"], ids["rear"]]],
    }


def dxf_bytes(doc):
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode()


def edited(raw, format, object_id, position, rotation=0):
    """Only native geometry changes. No CMS XDATA or IFC properties are altered."""
    if format == "dxf":
        doc = ezdxf.read(io.StringIO(raw.decode()))
        entity = next(
            e
            for e in doc.modelspace()
            if e.has_xdata("CMS_CAD") and e.get_xdata("CMS_CAD")[0].value == object_id
        )
        identity = list(entity.get_xdata("CMS_CAD"))
        entity.dxf.insert = position
        entity.dxf.rotation = -rotation
        assert list(entity.get_xdata("CMS_CAD")) == identity
        return dxf_bytes(doc)
    file = ifcopenshell.file.from_string(raw.decode())
    entity = file.by_guid(ifcopenshell.guid.compress(uuid.UUID(object_id).hex))
    identity = copy.deepcopy(get_psets(entity))
    axis = entity.ObjectPlacement.RelativePlacement
    axis.Location.Coordinates = tuple(float(x) for x in position)
    angle = -math.radians(rotation)
    axis.RefDirection.DirectionRatios = (math.cos(angle), math.sin(angle), 0.0)
    assert get_psets(entity) == identity
    return file.to_string().encode()


@pytest.mark.parametrize("format", ["dxf", "ifc"])
def test_roundtrip_retains_xyz_identity_and_native_length(exchange, format):
    raw = export_file(format, exchange)
    parsed = parse_file(format, raw)
    assert parsed["snapshot_id"] == exchange["snapshot_id"]
    rows = {o["id"]: o for o in parsed["objects"]}
    for original in exchange["objects"]:
        assert digest(rows[original["id"]]["geometry"]) == digest(original["geometry"])
        assert rows[original["id"]]["version"] == 2
    route = next(o for o in rows.values() if o["kind"] == "pathway_segment")["geometry"]["points"]
    assert sum(math.dist(a, b) for a, b in zip(route, route[1:])) == 7


@pytest.mark.parametrize("format", ["dxf", "ifc"])
@pytest.mark.parametrize("angle", [-90, 90, 360])
def test_native_pose_edit_not_metadata(exchange, format, angle):
    rack = exchange["objects"][1]
    raw = edited(export_file(format, exchange), format, rack["id"], [5, 4, 0.4], angle)
    parsed = (parse_dxf if format == "dxf" else parse_ifc)(raw)
    result = next(o for o in parsed["objects"] if o["id"] == rack["id"])
    assert result["geometry"] == {
        **rack["geometry"],
        "position": [5.0, 4.0, 0.4],
        "rotation": 0.0 if angle == 360 else float(angle),
    }
    if format == "ifc":
        # Ports inherit the true nested placement, including asymmetric front/rear.
        front = next(o for o in parsed["objects"] if o["id"] == exchange["objects"][3]["id"])
        expected = [5, 4.5, 0.75] if angle == 360 else [5.5 if angle == 90 else 4.5, 4, 0.75]
        assert front["geometry"]["position"] == expected


def test_nested_dxf_insert_and_millimetres(exchange):
    doc = ezdxf.read(io.StringIO(export_file("dxf", exchange).decode()))
    rack = next(
        e
        for e in doc.modelspace()
        if e.dxftype() == "INSERT" and e.get_xdata("CMS_CAD")[1].value == "rack"
    )
    parent = doc.blocks.new("PARENT")
    doc.modelspace().unlink_entity(rack)
    parent.add_entity(rack)
    doc.modelspace().add_blockref("PARENT", (1, 2, 0.1), dxfattribs={"rotation": 90})
    row = next(o for o in parse_dxf(dxf_bytes(doc))["objects"] if o["kind"] == "rack")
    assert row["geometry"]["position"] == [-2.0, 4.0, 0.4]
    assert row["geometry"]["rotation"] == 0
    # Unit conversion uses native coordinates, not app metadata.
    doc.units = 4
    row = next(o for o in parse_dxf(dxf_bytes(doc))["objects"] if o["kind"] == "rack")
    assert row["geometry"]["position"] == [-0.002, 0.004, 0.0004]
    assert row["geometry"]["size"] == [0.0006, 0.001, 0.002]


@pytest.mark.parametrize("mutation", ["mirror", "tilt", "scale", "duplicate", "shape", "origin"])
def test_unsupported_dxf_transforms_and_identity_rejected(exchange, mutation):
    doc = ezdxf.read(io.StringIO(export_file("dxf", exchange).decode()))
    entity = next(
        e
        for e in doc.modelspace()
        if e.dxftype() == "INSERT" and e.get_xdata("CMS_CAD")[1].value == "rack"
    )
    if mutation == "mirror":
        entity.dxf.xscale = -1
    elif mutation == "tilt":
        entity.dxf.extrusion = (0, 1, 0)
    elif mutation == "scale":
        entity.dxf.xscale = 2
    elif mutation == "duplicate":
        doc.modelspace().add_entity(entity.copy())
    elif mutation == "origin":
        doc.blocks[entity.dxf.name].block.dxf.base_point = (1, 0, 0)
    else:
        doc.blocks[entity.dxf.name][0].dxf.start = (0.1, 0.1, 0.1)
    with pytest.raises(ValidationError):
        parse_dxf(dxf_bytes(doc))


@pytest.mark.parametrize("mutation", ["duplicate", "shape", "tilt", "missing_units"])
def test_unsupported_ifc_geometry_rejected(exchange, mutation):
    file = ifcopenshell.file.from_string(export_file("ifc", exchange).decode())
    entity = file.by_guid(ifcopenshell.guid.compress(uuid.UUID(exchange["objects"][1]["id"]).hex))
    if mutation == "duplicate":
        file.by_type("IfcSpace")[0].GlobalId = entity.GlobalId
    elif mutation == "tilt":
        entity.ObjectPlacement.RelativePlacement.Axis.DirectionRatios = (0.0, 1.0, 0.0)
    elif mutation == "shape":
        entity.Representation.Representations[0].Items[0].Position.Location.Coordinates = (
            0.1,
            0.0,
            0.0,
        )
    else:
        file.by_type("IfcProject")[0].UnitsInContext = None
    with pytest.raises(ValidationError):
        parse_ifc(file.to_string().encode())


def test_ifc_schema_validation_and_port_connections(exchange):
    import ifcopenshell.validate

    file = ifcopenshell.file.from_string(export_file("ifc", exchange).decode())
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(file, logger, express_rules=True)
    assert not logger.statements, logger.statements
    assert len(file.by_type("IfcRelConnectsPorts")) == 3


def test_closed_dxf_paths_cannot_be_misreported_as_unchanged(exchange):
    doc = ezdxf.read(io.StringIO(export_file("dxf", exchange).decode()))
    entity = next(e for e in doc.modelspace() if e.dxftype() == "POLYLINE")
    entity.close(True)
    with pytest.raises(ValidationError, match="Closed"):
        parse_dxf(dxf_bytes(doc))

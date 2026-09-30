"""DXF R2013, registered XDATA identity, native INSERT and 3D POLYLINE geometry."""

from __future__ import annotations

import io
import uuid

from app.exceptions import ValidationError
from app.services.cad_geometry import (
    MAX_OBJECTS,
    PROFILE,
    UNIT_SCALE,
    parsed_document,
    point,
    points,
)

APP_ID = "CMS_CAD"


def export_dxf(manifest):
    import ezdxf

    doc = ezdxf.new("R2013")
    doc.units = 6
    doc.appids.new(APP_ID)
    doc.rootdict.add_xrecord(APP_ID).reset([(1, PROFILE), (2, manifest["snapshot_id"])])
    model = doc.modelspace()
    for obj in manifest["objects"]:
        kind, geometry = obj["kind"], obj["geometry"]
        layer = "CMS_" + kind.upper()
        if layer not in doc.layers:
            doc.layers.new(
                layer,
                dxfattribs={
                    "color": {
                        "rack": 5,
                        "device": 3,
                        "port": 1,
                        "cable": 4,
                        "pathway_segment": 2,
                    }.get(kind, 8)
                },
            )
        if kind == "pathway_segment":
            entity = model.add_polyline3d(geometry["points"], dxfattribs={"layer": layer})
        elif kind == "port":
            entity = model.add_point(geometry["position"], dxfattribs={"layer": layer})
        else:
            block = doc.blocks.new("CMS_" + uuid.UUID(obj["id"]).hex)
            if kind == "cable":
                for path in geometry["paths"]:
                    block.add_polyline3d(path)
                position, yaw = [0, 0, 0], 0
            else:
                w, d, h = geometry["size"]
                x0, y0 = (0, 0) if kind == "room" else (-w / 2, -d / 2)
                vertices = [
                    (x0 + x * w, y0 + y * d, z * h) for z in (0, 1) for y in (0, 1) for x in (0, 1)
                ]
                for a, b in [
                    (0, 1),
                    (1, 3),
                    (3, 2),
                    (2, 0),
                    (4, 5),
                    (5, 7),
                    (7, 6),
                    (6, 4),
                    (0, 4),
                    (1, 5),
                    (2, 6),
                    (3, 7),
                ]:
                    block.add_line(vertices[a], vertices[b])
                position, yaw = geometry["position"], -geometry["rotation"]
            entity = model.add_blockref(
                block.name, position, dxfattribs={"rotation": yaw, "layer": layer}
            )
            # Attributes aid manual identification; XDATA is the identity contract.
            entity.add_attrib(
                "APP_UUID", obj["id"], position, dxfattribs={"height": 0.05, "flags": 1}
            )
            entity.add_attrib(
                "APP_ID", obj["identifier"], position, dxfattribs={"height": 0.05, "flags": 1}
            )
        entity.set_xdata(
            APP_ID,
            [
                (1000, obj["id"]),
                (1000, kind),
                (1000, manifest["snapshot_id"]),
                (1071, obj["version"]),
                (1040, float(geometry.get("length_m", 0))),
            ],
        )
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def parse_dxf(raw):
    import ezdxf
    from ezdxf.math import Matrix44

    try:
        doc = ezdxf.read(io.StringIO(raw.decode("utf-8-sig")))
        scale = UNIT_SCALE.get(doc.units)
        if scale is None:
            raise ValidationError("DXF needs explicit supported units (m, mm, cm, inch or foot)")
        header = doc.rootdict.get(APP_ID)
        tags = list(header.tags) if header is not None else []
        snapshot_id = (
            str(uuid.UUID(tags[1].value)) if len(tags) == 2 and tags[0].value == PROFILE else None
        )
        objects, references, visited, attributes = [], 0, 0, []

        def transformed(p, matrix):
            return point([v * scale for v in matrix.transform(p)])

        def read_pose(matrix):
            origin = matrix.transform((0, 0, 0))
            axes = [matrix.transform_direction(v) for v in [(1, 0, 0), (0, 1, 0), (0, 0, 1)]]
            # Uniform document unit conversion affects lengths, not the rotation basis.
            from app.services.cad_geometry import pose

            m = [[axes[j][i] for j in range(3)] + [origin[i]] for i in range(3)] + [[0, 0, 0, 1]]
            return pose(m, scale)

        def walk(entities, parent, ancestry=()):
            nonlocal references, visited
            for entity in entities:
                visited += 1
                if visited > MAX_OBJECTS * 20 or len(ancestry) > 16:
                    raise ValidationError("DXF nesting or entity limit exceeded")
                tagged = entity.has_xdata(APP_ID)
                kind = entity.dxftype()
                if not tagged:
                    if kind == "INSERT":
                        name = entity.dxf.name
                        if name in ancestry or entity.mcount > 1:
                            raise ValidationError("Cyclic or array INSERT is unsupported")
                        block = doc.blocks.get(name)
                        if block is None:
                            raise ValidationError("Missing DXF block")
                        if block.block.is_xref:
                            raise ValidationError("External DXF references are unsupported")
                        walk(block, entity.matrix44() @ parent, (*ancestry, name))
                    else:
                        references += 1
                    continue
                data = list(entity.get_xdata(APP_ID))
                if len(data) != 5 or [v.code for v in data] != [1000, 1000, 1000, 1071, 1040]:
                    raise ValidationError("Invalid CAD identity XDATA")
                object_id = str(uuid.UUID(data[0].value))
                object_kind = data[1].value
                if snapshot_id is None or data[2].value != snapshot_id:
                    raise ValidationError("Missing or mixed CAD export snapshot IDs")
                geometry = {}
                if kind == "INSERT":
                    attributes.append(
                        [object_id, sorted((a.dxf.tag, a.dxf.text) for a in entity.attribs)]
                    )
                if kind == "POLYLINE" and entity.is_closed:
                    raise ValidationError(
                        "Closed CAD paths are unsupported; closing edges cannot be discarded"
                    )
                if kind in {"POLYLINE", "POINT"}:
                    read_pose(parent)
                    if tuple(entity.dxf.get("extrusion", (0, 0, 1))) != (0, 0, 1):
                        raise ValidationError("Non-default CAD extrusion is unsupported")
                if (
                    object_kind == "pathway_segment"
                    and kind == "POLYLINE"
                    and entity.is_3d_polyline
                ):
                    geometry = {
                        "points": points(
                            [transformed(v.dxf.location, parent) for v in entity.vertices]
                        ),
                        "length_m": float(data[4].value),
                    }
                elif object_kind == "port" and kind == "POINT":
                    geometry = {"position": transformed(entity.dxf.location, parent)}
                elif kind == "INSERT" and object_kind in {"room", "rack", "device", "cable"}:
                    if entity.mcount > 1:
                        raise ValidationError("Array INSERT duplicates a stable object ID")
                    matrix = entity.matrix44() @ parent
                    block = doc.blocks.get(entity.dxf.name)
                    if block is None:
                        raise ValidationError("Missing mapped block")
                    if block.block.is_xref or any(block.block.dxf.base_point):
                        raise ValidationError(
                            "External references or changed block origins are unsupported"
                        )
                    if object_kind == "cable":
                        if any(
                            e.dxftype() != "POLYLINE" or not e.is_3d_polyline or e.is_closed
                            for e in block
                        ):
                            raise ValidationError("Unsupported cable geometry")
                        geometry = {
                            "paths": [
                                points([transformed(v.dxf.location, matrix) for v in e.vertices])
                                for e in block
                            ]
                        }
                    else:
                        geometry = read_pose(matrix)
                        edges = list(block)
                        if len(edges) != 12 or any(e.dxftype() != "LINE" for e in edges):
                            raise ValidationError(
                                "Only unchanged proxy boxes and native INSERT poses are supported"
                            )
                        vertices = [tuple(v) for e in edges for v in (e.dxf.start, e.dxf.end)]
                        if (
                            len(
                                {
                                    tuple(sorted((a, b)))
                                    for a, b in zip(vertices[::2], vertices[1::2])
                                }
                            )
                            != 12
                        ):
                            raise ValidationError("Proxy box edges were changed")
                        low = [min(p[i] for p in vertices) for i in range(3)]
                        high = [max(p[i] for p in vertices) for i in range(3)]
                        # Reject box edits that keep extents but change the actual wire geometry.
                        if any(
                            sum(abs(a[i] - b[i]) > 1e-7 for i in range(3)) != 1
                            for a, b in zip(vertices[::2], vertices[1::2])
                        ):
                            raise ValidationError("Edited proxy shape is unsupported")
                        if len(set(vertices)) != 8 or any(
                            any(
                                abs(p[i] - low[i]) > 1e-7 and abs(p[i] - high[i]) > 1e-7
                                for i in range(3)
                            )
                            for p in vertices
                        ):
                            raise ValidationError("Edited proxy shape is unsupported")
                        expected = (
                            [0, 0, 0]
                            if object_kind == "room"
                            else [-(high[0] - low[0]) / 2, -(high[1] - low[1]) / 2, 0]
                        )
                        if any(abs(a - b) > 1e-7 for a, b in zip(low, expected)):
                            raise ValidationError(
                                "Proxy block origin changed; use INSERT placement"
                            )
                        geometry["size"] = point([(b - a) * scale for a, b in zip(low, high)])
                else:
                    raise ValidationError("Mapped CAD object changed to an unsupported entity type")
                objects.append(
                    {
                        "id": object_id,
                        "kind": object_kind,
                        "version": int(data[3].value),
                        "geometry": geometry,
                    }
                )

        walk(doc.modelspace(), Matrix44())
        return {**parsed_document(snapshot_id, objects, references), "readonly": sorted(attributes)}
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("DXF could not be parsed within the supported profile") from exc

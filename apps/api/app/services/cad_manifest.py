"""Build a complete, single-room exchange snapshot from scoped business records."""

from __future__ import annotations

import uuid

from app.exceptions import ValidationError
from app.models import Device, Location, PathwaySegment, Port, PortMapping, Rack
from app.services.cad_geometry import MAX_OBJECTS, PROFILE, normalized, rotation
from app.services.route_geometry import RouteGeometry
from app.services.scene_editor import SceneEditorService
from app.services.scene_routing import SceneRouteService


def manifest(editor: SceneEditorService, location_id: uuid.UUID, snapshot_id: uuid.UUID):
    scene = editor.scene.read(location_id=location_id, project_id=editor.principal.project_id)
    room = editor._get(Location, location_id)
    width, depth, height = editor._dimensions(room)
    if scene["truncated"]:
        raise ValidationError("Scene is truncated; select a smaller room before CAD export")
    if len(scene["locations"]) != 1:
        raise ValidationError("CAD v1 requires one room, not a floor or a room hierarchy")
    rows = []
    vertex_count = 0

    def account_vertices(count):
        nonlocal vertex_count
        vertex_count += count
        if vertex_count > 100000:
            raise ValidationError("CAD cumulative vertex budget exceeded; export a smaller room")

    def add(row, kind, identifier, geometry, **attributes):
        account_vertices(len(geometry.get("points", [])) or (8 if "size" in geometry else 1))
        rows.append(
            {
                "id": str(row.id),
                "version": row.version,
                "kind": kind,
                "identifier": identifier,
                "geometry": geometry,
                "attributes": attributes,
            }
        )

    add(
        room,
        "room",
        room.identifier,
        {"position": [0, 0, 0], "rotation": 0, "size": [width, depth, height]},
        name=room.name,
    )
    racks = {}
    for data in scene["racks"]:
        row = editor._get(Rack, uuid.UUID(data["id"]))
        racks[data["id"]] = row
        add(
            row,
            "rack",
            row.rack_identifier,
            {
                "position": [row.position_x, row.position_y, row.position_z],
                "rotation": rotation(row.rotation),
                "size": [
                    row.width_mm / 1000,
                    row.depth_mm / 1000,
                    row.height_u * 0.04445 + editor.RACK_FRAME_HEIGHT_M,
                ],
            },
            name=row.name,
            height_u=row.height_u,
        )
    devices = {}
    for data in scene["devices"]:
        row = editor._get(Device, uuid.UUID(data["id"]))
        devices[data["id"]] = row
        rack = racks.get(str(row.rack_id))
        # No invented global placement for unmounted equipment.
        if not rack:
            raise ValidationError("CAD export requires rack-mounted devices with known placement")
        add(
            row,
            "device",
            row.identifier,
            {
                "position": [
                    rack.position_x,
                    rack.position_y,
                    rack.position_z + 0.115 + (row.start_u - 1) * 0.04445,
                ],
                "rotation": rotation(rack.rotation),
                "size": [
                    (data["width_mm"] or 482) / 1000,
                    (data["depth_mm"] or 350) / 1000,
                    row.rack_units * 0.04445 - 0.003,
                ],
            },
            name=row.name,
            rack_id=str(rack.id),
            start_u=row.start_u,
            face=row.face,
            proxy=True,
            derived_geometry=True,
        )
    port_points = {}
    routing = SceneRouteService(editor)
    for data in scene["ports"]:
        port = editor._get(Port, uuid.UUID(data["id"]))
        device = devices[str(port.device_id)]
        rack = racks.get(str(device.rack_id))
        position = routing._port_point((port, device, rack))
        if position is None:
            raise ValidationError("CAD export cannot determine a schematic port anchor")
        port_points[str(port.id)] = position
        add(
            port,
            "port",
            port.identifier,
            {"position": position},
            device_id=str(device.id),
            face=port.front_or_rear,
            connector=port.connector_type,
            schematic_anchor=True,
            derived_geometry=True,
        )
    segments = {}
    for pathway in scene["pathways"]:
        for data in pathway["segments"]:
            row = editor._get(PathwaySegment, uuid.UUID(data["id"]))
            geometry = RouteGeometry.from_segment(row)
            segments[str(row.id)] = row
            add(
                row,
                "pathway_segment",
                pathway["identifier"] + " / " + row.name,
                {"points": geometry.points, "length_m": row.length_m},
                pathway_id=pathway["id"],
                geometry_hash=geometry.fingerprint,
            )
    warnings = []
    for cable in scene["cables"]:
        if cable["endpoint_scope"] != "complete" or cable["route_scope"] == "partial":
            warnings.append("A cable outside this room/project scope was omitted")
            continue
        endpoints = sorted(cable["terminations"], key=lambda t: t["side"])
        ids = [term["port_id"] for term in endpoints]
        if any(port not in port_points for port in ids):
            warnings.append("A cable with unavailable port geometry was omitted")
            continue
        paths = []
        portions = {p["segment_id"]: p for p in cable.get("route_portions", [])}
        for segment_id in cable["route_segment_ids"]:
            segment = segments[segment_id]
            part = portions.get(segment_id)
            geometry = RouteGeometry.from_segment(segment)
            if part:
                if not part["valid"]:
                    raise ValidationError(
                        "A saved cable route has stale geometry; resolve it before export"
                    )
                path = geometry.clip(part["start_offset_m"], part["end_offset_m"])
                account_vertices(len(path))
                paths.append([[p[k] for k in ("x", "y", "z")] for p in path])
            else:
                account_vertices(len(geometry.points))
                paths.append(list(geometry.points))
        if not paths:
            account_vertices(len(ids))
            paths = [[port_points[port] for port in ids]]
        rows.append(
            {
                "id": cable["id"],
                "version": cable["version"],
                "kind": "cable",
                "identifier": cable["identifier"],
                "geometry": {"paths": paths},
                "attributes": {
                    "port_ids": ids,
                    "route_portions": cable.get("route_portions", []),
                    "schematic": not cable["route_segment_ids"],
                    "registered_length_m": cable.get("length_m"),
                },
            }
        )
    if len(rows) > MAX_OBJECTS:
        raise ValidationError("CAD object limit exceeded")
    mappings = editor.db.scalars(
        editor.scene._query(PortMapping).where(
            PortMapping.source_port_id.in_([uuid.UUID(p) for p in port_points]),
            PortMapping.target_port_id.in_([uuid.UUID(p) for p in port_points]),
        )
    )
    return normalized(
        {
            "profile": PROFILE,
            "snapshot_id": str(snapshot_id),
            "location_id": str(location_id),
            "project_id": str(editor.principal.project_id) if editor.principal.project_id else None,
            "frame": {
                "units": "m",
                "up": "Z",
                "origin": [0, 0, 0],
                "scope": "room-local",
                "rotation": "clockwise-degrees",
            },
            "objects": rows,
            "port_mappings": [[str(m.source_port_id), str(m.target_port_id)] for m in mappings],
            "warnings": warnings,
            "limitations": [
                "Rack/device solids are planning proxies; ports are schematic anchors",
                "Cable access leads and measured lengths are not inferred from CAD",
                "No cross-room surveyed/global coordinates; no automatic new objects or deletion",
            ],
        }
    )

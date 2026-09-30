"""IFC4 Add2 TC1 exchange profile: planning proxies, axes and physical port relations."""

from __future__ import annotations

import math
import uuid

from app.exceptions import ValidationError
from app.services.cad_geometry import (
    MAX_OBJECTS,
    PROFILE,
    parsed_document,
    point,
    points,
    pose,
)


def export_ifc(manifest):
    import ifcopenshell
    import ifcopenshell.guid
    import numpy as np

    file = ifcopenshell.file(schema="IFC4")
    make = file.create_entity

    def gid(value):
        return ifcopenshell.guid.compress(uuid.UUID(value).hex)

    def derived(label):
        return gid(str(uuid.uuid5(uuid.UUID(manifest["location_id"]), label)))

    def xyz(value):
        return make("IfcCartesianPoint", Coordinates=tuple(float(v) for v in value))

    def direction(value):
        return make("IfcDirection", DirectionRatios=tuple(float(v) for v in value))

    def axis(position, x=(1.0, 0.0, 0.0)):
        return make(
            "IfcAxis2Placement3D",
            Location=xyz(position),
            Axis=direction((0, 0, 1)),
            RefDirection=direction(x),
        )

    context = make(
        "IfcGeometricRepresentationContext",
        ContextIdentifier="Model",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=1e-7,
        WorldCoordinateSystem=axis((0, 0, 0)),
    )
    unit = make("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    project = make(
        "IfcProject",
        GlobalId=derived("project"),
        Name="CMS room exchange",
        RepresentationContexts=[context],
        UnitsInContext=make("IfcUnitAssignment", Units=[unit]),
    )

    def properties(product, name, values):
        entries = [
            make(
                "IfcPropertySingleValue",
                Name=key,
                NominalValue=make("IfcInteger" if isinstance(value, int) else "IfcText", value),
            )
            for key, value in values.items()
        ]
        pset = make(
            "IfcPropertySet", GlobalId=ifcopenshell.guid.new(), Name=name, HasProperties=entries
        )
        make(
            "IfcRelDefinesByProperties",
            GlobalId=ifcopenshell.guid.new(),
            RelatedObjects=[product],
            RelatingPropertyDefinition=pset,
        )

    properties(
        project,
        "CMS_Exchange",
        {
            "Profile": PROFILE,
            "SnapshotId": manifest["snapshot_id"],
            "RoomId": manifest["location_id"],
            "Frame": "room-local metres Z-up; no surveyed global origin",
        },
    )
    products, positions = {}, {}
    for obj in manifest["objects"]:
        kind, geometry = obj["kind"], obj["geometry"]
        cls = {
            "room": "IfcSpace",
            "rack": "IfcBuildingElementProxy",
            "device": "IfcBuildingElementProxy",
            "port": "IfcDistributionPort",
            "pathway_segment": "IfcCableCarrierSegment",
            "cable": "IfcCableSegment",
        }[kind]
        position = geometry.get("position", [0, 0, 0])
        angle = -math.radians(geometry.get("rotation", 0))
        matrix = np.eye(4)
        matrix[:3, 3] = position
        matrix[:2, :2] = [[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]]
        parent_id = (
            obj["attributes"].get("rack_id")
            if kind == "device"
            else obj["attributes"].get("device_id")
            if kind == "port"
            else manifest["location_id"]
            if kind != "room"
            else None
        )
        parent = products.get(parent_id)
        local = np.linalg.inv(positions[parent_id]) @ matrix if parent else matrix
        placement = make(
            "IfcLocalPlacement",
            PlacementRelTo=parent.ObjectPlacement if parent else None,
            RelativePlacement=axis(local[:3, 3], local[:3, 0]),
        )
        args = dict(GlobalId=gid(obj["id"]), Name=obj["identifier"], ObjectPlacement=placement)
        if kind == "room":
            args.update(CompositionType="ELEMENT", PredefinedType="INTERNAL")
        if kind in {"rack", "device"}:
            args.update(ObjectType="CMS planning proxy", PredefinedType="USERDEFINED")
        if kind == "port":
            args.update(FlowDirection="SOURCEANDSINK")
        product = make(cls, **args)
        products[obj["id"]] = product
        positions[obj["id"]] = matrix
        properties(
            product,
            "CMS_Object",
            {
                "AppUUID": obj["id"],
                "Kind": kind,
                "Version": obj["version"],
                "SnapshotId": manifest["snapshot_id"],
                "LengthM": str(geometry.get("length_m", 0)),
                "GeometryNote": "Planning geometry; port anchors and un-routed cables are schematic",
            },
        )
        if kind in {"room", "rack", "device"}:
            w, d, h = geometry["size"]
            profile = make(
                "IfcRectangleProfileDef", ProfileType="AREA", XDim=float(w), YDim=float(d)
            )
            solid = make(
                "IfcExtrudedAreaSolid",
                SweptArea=profile,
                Position=axis((w / 2, d / 2, 0) if kind == "room" else (0, 0, 0)),
                ExtrudedDirection=direction((0, 0, 1)),
                Depth=float(h),
            )
            rep = make(
                "IfcShapeRepresentation",
                ContextOfItems=context,
                RepresentationIdentifier="Body",
                RepresentationType="SweptSolid",
                Items=[solid],
            )
        elif kind == "port":
            rep = make(
                "IfcShapeRepresentation",
                ContextOfItems=context,
                RepresentationIdentifier="Reference",
                RepresentationType="Point",
                Items=[xyz((0, 0, 0))],
            )
        else:
            paths = [geometry["points"]] if kind == "pathway_segment" else geometry["paths"]
            curves = [make("IfcPolyline", Points=[xyz(p) for p in path]) for path in paths]
            rep = make(
                "IfcShapeRepresentation",
                ContextOfItems=context,
                RepresentationIdentifier="Axis",
                RepresentationType="Curve3D",
                Items=curves,
            )
        product.Representation = make("IfcProductDefinitionShape", Representations=[rep])
        if kind == "room":
            make(
                "IfcRelAggregates",
                GlobalId=derived("room-aggregation"),
                RelatingObject=project,
                RelatedObjects=[product],
            )
        elif kind == "port":
            make(
                "IfcRelNests",
                GlobalId=derived("port-parent:" + obj["id"]),
                RelatingObject=parent,
                RelatedObjects=[product],
            )
        else:
            make(
                "IfcRelContainedInSpatialStructure",
                GlobalId=derived("containment:" + obj["id"]),
                RelatedElements=[product],
                RelatingStructure=products[manifest["location_id"]],
            )
    for obj in manifest["objects"]:
        if obj["kind"] != "cable":
            continue
        for index, port_id in enumerate(obj["attributes"]["port_ids"]):
            endpoint = products[port_id]
            cable_port = make(
                "IfcDistributionPort",
                GlobalId=derived(obj["id"] + f":{index}"),
                Name=f"Cable end {'AB'[index]}",
                FlowDirection="SOURCEANDSINK",
                ObjectPlacement=endpoint.ObjectPlacement,
            )
            make(
                "IfcRelNests",
                GlobalId=derived(obj["id"] + f":nest:{index}"),
                RelatingObject=products[obj["id"]],
                RelatedObjects=[cable_port],
            )
            # Each IFC port permits at most one incoming and one outgoing relation.
            # A patch panel rear mapping already occupies its incoming relation.
            rear_mapped = port_id in {b for _, b in manifest["port_mappings"]}
            make(
                "IfcRelConnectsPorts",
                GlobalId=derived(obj["id"] + f":connect:{index}"),
                RelatingPort=endpoint if rear_mapped else cable_port,
                RelatedPort=cable_port if rear_mapped else endpoint,
                RealizingElement=products[obj["id"]],
            )
    for a, b in manifest["port_mappings"]:
        make(
            "IfcRelConnectsPorts",
            GlobalId=derived("mapping:" + a + ":" + b),
            Name="Registered front/rear mapping",
            RelatingPort=products[a],
            RelatedPort=products[b],
        )
    return file.to_string().encode("utf-8")


def parse_ifc(raw):
    import ifcopenshell
    import ifcopenshell.guid
    import numpy as np
    from ifcopenshell.util.element import get_psets
    from ifcopenshell.util.placement import get_axis2placement, get_local_placement
    from ifcopenshell.util.unit import calculate_unit_scale

    try:
        file = ifcopenshell.file.from_string(raw.decode("utf-8-sig"))
        if file.schema != "IFC4":
            raise ValidationError("Only the IFC4 Add2 TC1 profile is supported")
        if len(list(file)) > MAX_OBJECTS * 50:
            raise ValidationError("IFC entity limit exceeded")
        projects = file.by_type("IfcProject")
        if len(projects) != 1:
            raise ValidationError("IFC must contain exactly one project")
        header = get_psets(projects[0]).get("CMS_Exchange", {})
        snapshot_id = (
            str(uuid.UUID(header["SnapshotId"])) if header.get("Profile") == PROFILE else None
        )
        units = projects[0].UnitsInContext
        if (
            units is None
            or len([u for u in units.Units if getattr(u, "UnitType", None) == "LENGTHUNIT"]) != 1
        ):
            raise ValidationError("IFC must declare exactly one length unit")
        scale = calculate_unit_scale(file)
        if not math.isfinite(scale) or not 1e-6 <= scale <= 1000:
            raise ValidationError("Unsupported IFC units")
        roots = file.by_type("IfcRoot")
        ids = [r.GlobalId for r in roots]
        if len(set(ids)) != len(ids):
            raise ValidationError("Duplicate IFC GlobalId")
        objects, references, attributes = [], 0, []
        for product in file.by_type("IfcProduct"):
            properties = get_psets(product).get("CMS_Object")
            if not properties:
                references += 1
                continue
            oid = str(uuid.UUID(properties["AppUUID"]))
            kind = properties["Kind"]
            if product.GlobalId != ifcopenshell.guid.compress(uuid.UUID(oid).hex):
                raise ValidationError("IFC GlobalId conflicts with the application UUID")
            if snapshot_id is None or properties["SnapshotId"] != snapshot_id:
                raise ValidationError("Missing or mixed IFC export snapshot IDs")
            expected_type = {
                "room": "IfcSpace",
                "rack": "IfcBuildingElementProxy",
                "device": "IfcBuildingElementProxy",
                "port": "IfcDistributionPort",
                "cable": "IfcCableSegment",
                "pathway_segment": "IfcCableCarrierSegment",
            }.get(kind)
            if not expected_type or not product.is_a(expected_type):
                raise ValidationError("Mapped IFC object changed class")
            seen, placement = set(), product.ObjectPlacement
            while placement:
                if (
                    not placement.is_a("IfcLocalPlacement")
                    or placement.id() in seen
                    or len(seen) >= 16
                ):
                    raise ValidationError("Unsupported, cyclic or excessive IFC placement nesting")
                seen.add(placement.id())
                placement = placement.PlacementRelTo
            matrix = get_local_placement(product.ObjectPlacement)
            geometry = pose(matrix, scale)
            reps = product.Representation.Representations if product.Representation else []
            if len(reps) != 1:
                raise ValidationError("Only one native profile representation is supported")
            rep = reps[0]
            context = rep.ContextOfItems
            if context.is_a("IfcGeometricRepresentationSubContext"):
                context = context.ParentContext
            if not np.allclose(
                get_axis2placement(context.WorldCoordinateSystem), np.eye(4), atol=1e-7
            ):
                raise ValidationError(
                    "Changed IFC world origin must be explicitly mapped before synchronization"
                )
            items = rep.Items
            if kind in {"room", "rack", "device"}:
                if len(items) != 1 or not items[0].is_a("IfcExtrudedAreaSolid"):
                    raise ValidationError("Edited proxy representation is unsupported")
                solid = items[0]
                profile = solid.SweptArea
                if (
                    not profile.is_a("IfcRectangleProfileDef")
                    or profile.Position is not None
                    or list(solid.ExtrudedDirection.DirectionRatios) != [0.0, 0.0, 1.0]
                ):
                    raise ValidationError("Edited proxy profile or extrusion is unsupported")
                size = [float(profile.XDim), float(profile.YDim), float(solid.Depth)]
                expected = np.eye(4)
                if kind == "room":
                    expected[:3, 3] = [size[0] / 2, size[1] / 2, 0]
                if not np.allclose(get_axis2placement(solid.Position), expected, atol=1e-7):
                    raise ValidationError("Proxy body moved independently; use ObjectPlacement")
                geometry["size"] = point([v * scale for v in size])
            elif kind == "port":
                if (
                    len(items) != 1
                    or not items[0].is_a("IfcCartesianPoint")
                    or any(items[0].Coordinates)
                ):
                    raise ValidationError("Edited port representation is unsupported")
                geometry = {"position": geometry["position"]}
            else:
                paths = []
                for item in items:
                    if not item.is_a("IfcPolyline"):
                        raise ValidationError("Only native IFC polyline axes are supported")
                    paths.append(
                        points(
                            [
                                (matrix @ np.array([*point(p.Coordinates), 1]))[:3] * scale
                                for p in item.Points
                            ]
                        )
                    )
                if kind == "pathway_segment":
                    if len(paths) != 1:
                        raise ValidationError("A mapped tray segment needs one polyline")
                    geometry = {"points": paths[0], "length_m": float(properties["LengthM"])}
                else:
                    geometry = {"paths": paths}
            attributes.append(
                [
                    oid,
                    product.Name,
                    product.ObjectType,
                    properties.get("GeometryNote"),
                    getattr(product, "FlowDirection", None),
                    getattr(product, "PredefinedType", None),
                ]
            )
            objects.append(
                {
                    "id": oid,
                    "kind": kind,
                    "version": int(properties["Version"]),
                    "geometry": geometry,
                }
            )
        relations = []
        for kind, parent, children in [
            ("IfcRelNests", "RelatingObject", "RelatedObjects"),
            ("IfcRelAggregates", "RelatingObject", "RelatedObjects"),
            ("IfcRelContainedInSpatialStructure", "RelatingStructure", "RelatedElements"),
        ]:
            for rel in file.by_type(kind):
                relations.append(
                    [
                        kind,
                        getattr(rel, parent).GlobalId,
                        sorted(o.GlobalId for o in getattr(rel, children)),
                    ]
                )
        for rel in file.by_type("IfcRelConnectsPorts"):
            relations.append(
                [
                    "connection",
                    rel.RelatingPort.GlobalId,
                    rel.RelatedPort.GlobalId,
                    rel.RealizingElement.GlobalId if rel.RealizingElement else "",
                ]
            )
        return {
            **parsed_document(snapshot_id, objects, references),
            "readonly": {"attributes": sorted(attributes), "relations": sorted(relations)},
        }
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("IFC could not be parsed within the supported profile") from exc

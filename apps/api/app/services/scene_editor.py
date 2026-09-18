"""Transactional, scope-aware scene creation and physical rack placement."""

from __future__ import annotations

import math
import uuid
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models import (
    Device,
    DeviceTemplate,
    Location,
    LocationType,
    Pathway,
    PathwaySegment,
    Port,
    Rack,
    utcnow,
)
from app.schemas import CableCreate, DeviceCreate, PathwayCreate
from app.schemas_scene_editor import (
    CablePolicyUpdate,
    RackGridCreate,
    RackPoseUpdate,
    RoomCreate,
    RoomEntrance,
    RoomEntrancesUpdate,
)
from app.security import Principal, require_permission
from app.services.connectivity import ConnectivityService
from app.services.infrastructure import InfrastructureService
from app.services.scene import SceneService


class SceneEditorService:
    # Match the visible enclosure, including the renderer's half-thickness top frame.
    RACK_FRAME_HEIGHT_M = 0.1925
    MIN_RACK_WIDTH_M = 0.5
    MIN_RACK_DEPTH_M = 0.45

    def __init__(self, db: Session, principal: Principal):
        self.db, self.principal = db, principal
        self.scene = SceneService(db, principal)
        self.infrastructure = InfrastructureService(db, principal)

    def _get(self, model, object_id: uuid.UUID):
        value = self.db.scalar(
            self.scene._query(model)
            .where(model.id == object_id)
            .execution_options(populate_existing=True)
        )
        if value is None:
            raise NotFoundError(f"{model.__name__} not found in tenant")
        return value

    def _scope(self, location_id: uuid.UUID | None, permission: str, project_id=None):
        project_id = project_id or self.principal.project_id
        actor = self.scene._authorize(location_id, project_id)
        self.principal = self.scene.principal
        self.infrastructure.principal = self.principal
        require_permission(actor, permission)
        return self._get(Location, location_id) if location_id else None

    def _lock_location(self, location_id: uuid.UUID) -> Location:
        # Serialise collision/U checks with other scene edits in this room.
        # SQLite ignores FOR UPDATE: acquire its writer lock without changing data.
        if self.db.get_bind().dialect.name == "sqlite":
            self.db.execute(
                update(Location)
                .where(
                    Location.id == location_id,
                    Location.tenant_id == self.principal.tenant_id,
                    Location.deleted_at.is_(None),
                )
                .values(version=Location.version, updated_at=Location.updated_at)
                .execution_options(synchronize_session=False)
            )
        return self.db.scalar(
            self.scene._query(Location)
            .where(Location.id == location_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _dimensions(location: Location) -> tuple[float, float, float]:
        result = []
        for key in ("width_m", "depth_m", "height_m"):
            value = location.dimensions.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValidationError(
                    "Room needs finite positive width_m, depth_m and height_m before spatial editing"
                )
            result.append(float(value))
        return tuple(result)

    @classmethod
    def _footprint(cls, rack: dict[str, Any]):
        angle = math.radians(rack["rotation"])
        c, s = math.cos(angle), math.sin(angle)
        half_width = max(cls.MIN_RACK_WIDTH_M, rack["width_mm"] / 1000) / 2
        half_depth = max(cls.MIN_RACK_DEPTH_M, rack["depth_mm"] / 1000) / 2
        corners = [
            (rack["position_x"] + dx * c + dy * s, rack["position_y"] - dx * s + dy * c)
            for dx, dy in (
                (-half_width, -half_depth),
                (half_width, -half_depth),
                (half_width, half_depth),
                (-half_width, half_depth),
            )
        ]
        return corners, ((c, -s), (s, c))

    @staticmethod
    def _pose(rack: Rack):
        return {
            key: getattr(rack, key)
            for key in (
                "position_x",
                "position_y",
                "position_z",
                "rotation",
                "width_mm",
                "depth_mm",
                "height_u",
            )
        }

    def _validate_placement(self, candidate, existing, dimensions):
        width, depth, height = dimensions
        corners, axes = self._footprint(candidate)
        top = candidate["position_z"] + candidate["height_u"] * 0.04445 + self.RACK_FRAME_HEIGHT_M
        if (
            candidate["position_z"] < 0
            or top > height + 1e-8
            or any(
                x < -1e-8 or x > width + 1e-8 or y < -1e-8 or y > depth + 1e-8 for x, y in corners
            )
        ):
            raise ValidationError("Rack footprint or height extends outside the room")
        for other in existing:
            other_top = other["position_z"] + other["height_u"] * 0.04445 + self.RACK_FRAME_HEIGHT_M
            if min(top, other_top) <= max(candidate["position_z"], other["position_z"]) + 1e-8:
                continue
            other_corners, other_axes = self._footprint(other)
            # Separating axis test permits touching faces but rejects rotated overlap.
            separated = False
            for ax, ay in (*axes, *other_axes):
                a = [x * ax + y * ay for x, y in corners]
                b = [x * ax + y * ay for x, y in other_corners]
                if min(max(a), max(b)) <= max(min(a), min(b)) + 1e-8:
                    separated = True
                    break
            if not separated:
                raise ConflictError("Rack footprint overlaps an existing or requested rack")

    def create_room(self, body: RoomCreate):
        self._scope(body.parent_id, "location:create")
        entrances = body.entrances
        if entrances is None:
            door_width = min(1.0, body.width_m)
            entrances = [
                RoomEntrance(
                    name="Main entrance",
                    wall="south",
                    offset_m=(body.width_m - door_width) / 2,
                    width_m=door_width,
                    height_m=min(2.1, body.height_m),
                )
            ]
        openings = self._entrances(entrances, (body.width_m, body.depth_m, body.height_m))
        return self.infrastructure.create_location(
            parent_id=body.parent_id,
            identifier=body.identifier,
            name=body.name,
            location_type=LocationType.DATA_HALL
            if body.kind == "server_room"
            else LocationType.ROOM,
            dimensions={
                "width_m": body.width_m,
                "depth_m": body.depth_m,
                "height_m": body.height_m,
                "entrances": openings,
            },
        )

    def create_racks(self, body: RackGridCreate):
        self._scope(body.location_id, "rack:create")
        location = self._lock_location(body.location_id)
        dimensions = self._dimensions(location)
        existing = [
            self._pose(row)
            for row in self.db.scalars(
                self.scene._query(Rack).where(Rack.location_id == location.id)
            ).all()
        ]
        angle = math.radians(body.rotation)
        width_m = max(self.MIN_RACK_WIDTH_M, body.width_mm / 1000)
        depth_m = max(self.MIN_RACK_DEPTH_M, body.depth_mm / 1000)
        step_x = abs(math.cos(angle)) * width_m + abs(math.sin(angle)) * depth_m + body.gap_m
        step_y = abs(math.sin(angle)) * width_m + abs(math.cos(angle)) * depth_m + body.gap_m
        poses = []
        for index in range(body.count):
            pose = dict(
                position_x=body.position_x + index % body.columns * step_x,
                position_y=body.position_y + index // body.columns * step_y,
                position_z=0,
                rotation=body.rotation,
                width_mm=body.width_mm,
                depth_mm=body.depth_mm,
                height_u=body.height_u,
            )
            self._validate_placement(pose, [*existing, *poses], dimensions)
            poses.append(pose)
        rows = []
        for index, pose in enumerate(poses, start=1):
            rows.append(
                self.infrastructure.create_rack(
                    location_id=location.id,
                    rack_identifier=f"{body.identifier_prefix}-{index:02d}",
                    name=f"{body.name_prefix} {index:02d}",
                    **pose,
                )
            )
        return rows

    def move_rack(self, rack_id: uuid.UUID, body: RackPoseUpdate):
        rack = self._get(Rack, rack_id)
        self._scope(rack.location_id, "rack:update")
        location = self._lock_location(rack.location_id)
        self.db.refresh(rack)
        if rack.version != body.expected_version:
            raise ConflictError("Rack version changed; reload before saving")
        before = self._pose(rack)
        pose = {**before, **body.model_dump(exclude={"expected_version"})}
        neighbours = [
            self._pose(row)
            for row in self.db.scalars(
                self.scene._query(Rack).where(
                    Rack.location_id == rack.location_id,
                    Rack.id != rack.id,
                )
            ).all()
        ]
        self._validate_placement(pose, neighbours, self._dimensions(location))
        result = self.db.execute(
            update(Rack)
            .where(
                Rack.id == rack.id,
                Rack.tenant_id == self.principal.tenant_id,
                Rack.version == body.expected_version,
                Rack.deleted_at.is_(None),
            )
            .values(
                **body.model_dump(exclude={"expected_version"}),
                version=body.expected_version + 1,
                updated_at=utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ConflictError("Rack version changed; reload before saving")
        self.db.refresh(rack)
        record_audit(
            self.db,
            principal=self.principal,
            action="rack.position_updated",
            object_type="rack",
            object_id=rack.id,
            before=before,
            after=pose,
            project_id=self.principal.project_id,
        )
        return rack

    @staticmethod
    def _text(value: str, label: str, minimum=1, maximum=180):
        if not minimum <= len(value.strip()) <= maximum:
            raise ValidationError(f"{label} must have {minimum} to {maximum} characters")

    def create_device(self, body: DeviceCreate):
        rack = self._get(Rack, body.rack_id)
        self._scope(rack.location_id, "device:create")
        self._lock_location(rack.location_id)
        template = self._get(DeviceTemplate, body.template_id)
        self._text(body.identifier, "Device identifier", minimum=3)
        self._text(body.name, "Device name")
        if body.face not in {"front", "rear"}:
            raise ValidationError("Device face must be front or rear")
        if template.width_mm > rack.width_mm or template.depth_mm > rack.depth_mm:
            raise ValidationError("Device template dimensions exceed rack dimensions")
        return self.infrastructure.create_device_from_template(**body.model_dump())

    def create_pathway(self, body: PathwayCreate):
        location = self._scope(body.location_id, "pathway:create")
        dimensions = self._dimensions(location)
        self._text(body.identifier, "Pathway identifier", minimum=3)
        self._text(body.name, "Pathway name")
        self._text(body.pathway_type, "Pathway type", maximum=80)
        if not 1 <= len(body.segments) <= 100:
            raise ValidationError("Pathway needs 1 to 100 segments")
        if body.capacity_area_mm2 is not None and (
            not math.isfinite(body.capacity_area_mm2) or body.capacity_area_mm2 <= 0
        ):
            raise ValidationError("Pathway capacity must be finite and positive")
        segments = []
        sequences = set()
        for index, segment in enumerate(body.segments, start=1):
            self._text(segment.name, "Segment name")
            if not 2 <= len(segment.coordinates) <= 512:
                raise ValidationError("Each pathway segment needs 2 to 512 coordinates")
            coordinates = []
            for point in segment.coordinates:
                if not {"x", "y"} <= point.keys() or point.keys() - {"x", "y", "z"}:
                    raise ValidationError("Pathway coordinates require x/y and optional z")
                xyz = {axis: point.get(axis, 0) for axis in ("x", "y", "z")}
                if any(
                    not math.isfinite(value) or value < 0 or value > limit
                    for value, limit in zip(xyz.values(), dimensions)
                ):
                    raise ValidationError(
                        "Pathway coordinate lies outside room bounds or is not finite"
                    )
                coordinates.append(xyz)
            distance = sum(
                math.dist(tuple(a.values()), tuple(b.values()))
                for a, b in zip(coordinates, coordinates[1:])
            )
            if distance <= 0:
                raise ValidationError("Pathway segment must have non-zero spatial length")
            if not math.isfinite(segment.length_m) or segment.length_m < 0:
                raise ValidationError("Segment length must be finite and non-negative")
            if (
                not math.isfinite(segment.reserved_percent)
                or not 0 <= segment.reserved_percent <= 100
            ):
                raise ValidationError("Reserved percentage must be between 0 and 100")
            if segment.capacity_area_mm2 is not None and (
                not math.isfinite(segment.capacity_area_mm2) or segment.capacity_area_mm2 <= 0
            ):
                raise ValidationError("Segment capacity must be finite and positive")
            sequence = segment.sequence if segment.sequence is not None else index
            if sequence < 1 or sequence in sequences:
                raise ValidationError("Segment sequences must be positive and unique")
            sequences.add(sequence)
            segments.append(
                {
                    **segment.model_dump(),
                    "sequence": sequence,
                    "coordinates": coordinates,
                    "length_m": segment.length_m or distance,
                }
            )
        data = body.model_dump(exclude={"segments"})
        return self.infrastructure.create_pathway(**data, segments=segments)

    def create_cable(self, body: CableCreate):
        project_id = body.project_id or self.principal.project_id
        if project_id is None:
            raise ValidationError("Select a project before creating a cable")
        self._text(body.identifier, "Cable identifier", minimum=3)
        self._text(body.media_type, "Cable media", maximum=80)
        self._text(body.construction, "Cable construction", maximum=80)
        if body.length_m is not None and (not math.isfinite(body.length_m) or body.length_m < 0):
            raise ValidationError("Cable length must be finite and non-negative")
        if body.color is not None:
            self._text(body.color, "Cable color", maximum=50)
        for port_id in (body.port_a_id, body.port_b_id):
            port = self._get(Port, port_id)
            device = self._get(Device, port.device_id)
            self._scope(device.location_id, "cable:create", project_id)
            if device.rack_id:
                rack = self._get(Rack, device.rack_id)
                if rack.location_id != device.location_id:
                    raise ConflictError("Device rack and location are inconsistent")
        if len(body.route_segment_ids) > 100 or len(set(body.route_segment_ids)) != len(
            body.route_segment_ids
        ):
            raise ValidationError("Cable route must contain at most 100 distinct segments")
        for segment_id in body.route_segment_ids:
            segment = self._get(PathwaySegment, segment_id)
            pathway = self._get(Pathway, segment.pathway_id)
            self._scope(pathway.location_id, "cable:create", project_id)
        # Policy edits use the same room lock; revalidate after acquiring it.
        location_ids = {
            self._get(Device, self._get(Port, port_id).device_id).location_id
            for port_id in (body.port_a_id, body.port_b_id)
        }
        for location_id in sorted(location_ids, key=str):
            self._lock_location(location_id)
        from app.services.scene_routing import SceneRouteService

        SceneRouteService(self).validate_for_save(
            body.model_copy(update={"project_id": project_id})
        )
        return ConnectivityService(self.db, self.principal).create_cable(
            **{**body.model_dump(), "project_id": project_id},
        )

    @staticmethod
    def _entrances(entrances: list[RoomEntrance], dimensions):
        width, depth, height = dimensions
        ids = set()
        intervals: dict[str, list[tuple[float, float]]] = {}
        for entrance in entrances:
            wall_length = width if entrance.wall in {"north", "south"} else depth
            start, end = entrance.offset_m, entrance.offset_m + entrance.width_m
            if entrance.id in ids:
                raise ValidationError("Entrance identifiers must be unique")
            ids.add(entrance.id)
            if end > wall_length + 1e-8 or entrance.height_m > height + 1e-8:
                raise ValidationError("Entrance extends beyond its wall or room height")
            spans = intervals.setdefault(entrance.wall, [])
            if any(
                min(end, other_end) > max(start, other_start) + 1e-8
                for other_start, other_end in spans
            ):
                raise ValidationError("Entrances on the same wall must not overlap")
            spans.append((start, end))
        return [entrance.model_dump() for entrance in entrances]

    def _metadata_update(self, row, expected_version: int, changes: dict, action: str):
        self.db.refresh(row)
        if row.version != expected_version:
            raise ConflictError("Object version changed; reload before saving")
        model = type(row)
        before = {key: getattr(row, key) for key in changes}
        result = self.db.execute(
            update(model)
            .where(
                model.id == row.id,
                model.tenant_id == self.principal.tenant_id,
                model.version == expected_version,
                model.deleted_at.is_(None),
            )
            .values(**changes, version=expected_version + 1, updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ConflictError("Object version changed; reload before saving")
        self.db.refresh(row)
        record_audit(
            self.db,
            principal=self.principal,
            action=action,
            object_type=row.__tablename__.removesuffix("s"),
            object_id=row.id,
            before=before,
            after=changes,
            project_id=self.principal.project_id,
        )
        return {"id": str(row.id), "version": row.version}

    def update_policy(self, model, object_id: uuid.UUID, body: CablePolicyUpdate):
        row = self._get(model, object_id)
        permission = "device:update" if model is Device else "pathway:update"
        self._scope(row.location_id, permission)
        self._lock_location(row.location_id)
        self.db.refresh(row)
        policy = body.model_dump(exclude={"expected_version"})
        changes = (
            {"instance_overrides": {**row.instance_overrides, "cable_policy": policy}}
            if model is Device
            else {"cable_policy": policy}
        )
        return self._metadata_update(
            row,
            body.expected_version,
            changes,
            f"{model.__tablename__.removesuffix('s')}.cable_policy_updated",
        )

    def update_entrances(self, room_id: uuid.UUID, body: RoomEntrancesUpdate):
        self._scope(room_id, "location:update")
        room = self._lock_location(room_id)
        if room.location_type not in {
            LocationType.ROOM,
            LocationType.TR,
            LocationType.ER,
            LocationType.MDF,
            LocationType.MMR,
            LocationType.DATA_HALL,
            LocationType.ENTRANCE_FACILITY,
        }:
            raise ValidationError("Entrances can only be assigned to room locations")
        openings = self._entrances(body.entrances, self._dimensions(room))
        return self._metadata_update(
            room,
            body.expected_version,
            {"dimensions": {**room.dimensions, "entrances": openings}},
            "location.entrances_updated",
        )

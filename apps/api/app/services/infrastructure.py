from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import AuthorizationError, ConflictError, ValidationError
from app.models import (
    Device,
    DeviceTemplate,
    Location,
    Pathway,
    PathwaySegment,
    Port,
    PortMapping,
    Rack,
)
from app.security import Principal, require_permission
from app.services.resource_scope import ResourceScope


@dataclass(frozen=True)
class ElevationDevice:
    id: uuid.UUID
    identifier: str
    name: str
    device_type: str
    start_u: int
    rack_units: int
    face: str
    ports: int


class InfrastructureService:
    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.principal = principal

    def _authorization(self, permission: str) -> ResourceScope:
        scope = ResourceScope(self.session, self.principal)
        require_permission(scope.principal, permission)
        return scope

    def create_location(
        self,
        *,
        location_type,
        identifier: str,
        name: str,
        parent_id: uuid.UUID | None = None,
        dimensions: dict | None = None,
        coordinates: dict | None = None,
        transform_3d: dict | None = None,
        floor_plan_reference: str | None = None,
    ) -> Location:
        scope = self._authorization("location:create")
        if parent_id:
            scope.require(scope.get(Location, parent_id), "location:create")
        elif not scope.principal.is_tenant_member:
            raise AuthorizationError("Only workspace members can create top-level locations")
        location = Location(
            tenant_id=self.principal.tenant_id,
            parent_id=parent_id,
            location_type=location_type,
            identifier=identifier,
            name=name,
            dimensions=dimensions or {},
            coordinates=coordinates or {},
            transform_3d=transform_3d or {},
            floor_plan_reference=floor_plan_reference,
        )
        self.session.add(location)
        self.session.flush()
        record_audit(
            self.session,
            principal=self.principal,
            action="location.created",
            object_type="location",
            object_id=location.id,
            after={"identifier": identifier, "type": str(location_type)},
        )
        return location

    def create_rack(
        self,
        *,
        location_id: uuid.UUID,
        rack_identifier: str,
        name: str,
        height_u: int = 42,
        width_mm: int = 600,
        depth_mm: int = 1000,
        position_x: float = 0,
        position_y: float = 0,
        position_z: float = 0,
        rotation: float = 0,
        reserved_units: list[int] | None = None,
    ) -> Rack:
        scope = self._authorization("rack:create")
        if not 1 <= height_u <= 60:
            raise ValidationError("Rack height must be between 1U and 60U")
        scope.require(scope.get(Location, location_id), "rack:create")
        reserved = sorted(set(reserved_units or []))
        if any(u < 1 or u > height_u for u in reserved):
            raise ValidationError("Reserved U positions must be inside the rack")
        rack = Rack(
            tenant_id=self.principal.tenant_id,
            location_id=location_id,
            rack_identifier=rack_identifier,
            name=name,
            height_u=height_u,
            width_mm=width_mm,
            depth_mm=depth_mm,
            position_x=position_x,
            position_y=position_y,
            position_z=position_z,
            rotation=rotation,
            reserved_units=reserved,
        )
        self.session.add(rack)
        self.session.flush()
        record_audit(
            self.session,
            principal=self.principal,
            action="rack.created",
            object_type="rack",
            object_id=rack.id,
            after={"identifier": rack_identifier, "height_u": height_u},
        )
        return rack

    def create_template(
        self,
        *,
        manufacturer: str,
        model: str,
        device_type: str,
        rack_units: int,
        port_blueprint: list[dict[str, Any]],
        width_mm: int = 482,
        depth_mm: int = 350,
        height_mm: int | None = None,
        model_3d_reference: str | None = None,
    ) -> DeviceTemplate:
        scope = self._authorization("device:create")
        if not scope.principal.is_tenant_member:
            raise AuthorizationError("Only workspace members can create device templates")
        if rack_units < 1 or rack_units > 20:
            raise ValidationError("Template rack units must be between 1 and 20")
        template = DeviceTemplate(
            tenant_id=self.principal.tenant_id,
            manufacturer=manufacturer,
            model=model,
            device_type=device_type,
            rack_units=rack_units,
            width_mm=width_mm,
            depth_mm=depth_mm,
            height_mm=height_mm,
            port_blueprint=port_blueprint,
            model_3d_reference=model_3d_reference,
        )
        self.session.add(template)
        self.session.flush()
        record_audit(
            self.session,
            principal=self.principal,
            action="device_template.created",
            object_type="device_template",
            object_id=template.id,
            after={"manufacturer": manufacturer, "model": model},
        )
        return template

    def create_device_from_template(
        self,
        *,
        rack_id: uuid.UUID,
        template_id: uuid.UUID,
        identifier: str,
        name: str,
        start_u: int,
        face: str = "front",
    ) -> Device:
        scope = self._authorization("device:create")
        rack = scope.get(Rack, rack_id)
        template = scope.get(DeviceTemplate, template_id)
        scope.get(Location, rack.location_id)
        scope.require(rack, "device:create")
        end_u = start_u + template.rack_units - 1
        if start_u < 1 or end_u > rack.height_u:
            raise ValidationError("Device exceeds rack height")
        if set(range(start_u, end_u + 1)).intersection(rack.reserved_units):
            raise ConflictError("Device occupies a reserved rack U position")
        overlap = self.session.scalar(
            select(Device.id).where(
                Device.rack_id == rack_id,
                Device.tenant_id == self.principal.tenant_id,
                Device.deleted_at.is_(None),
                Device.face == face,
                and_(
                    Device.start_u <= end_u,
                    Device.start_u + Device.rack_units - 1 >= start_u,
                ),
            )
        )
        if overlap:
            raise ConflictError("Device overlaps an occupied rack U position")
        device = Device(
            tenant_id=self.principal.tenant_id,
            rack_id=rack.id,
            location_id=rack.location_id,
            template_id=template.id,
            identifier=identifier,
            name=name,
            device_type=template.device_type,
            rack_units=template.rack_units,
            start_u=start_u,
            face=face,
        )
        self.session.add(device)
        self.session.flush()
        grouped: dict[str, list[Port]] = {}
        for blueprint in template.port_blueprint:
            count = int(blueprint.get("count", 0))
            prefix = str(blueprint.get("prefix", "P"))
            start = int(blueprint.get("start", 1))
            for offset in range(count):
                number = start + offset
                port = Port(
                    tenant_id=self.principal.tenant_id,
                    device_id=device.id,
                    identifier=f"{prefix}{number:02d}",
                    label=f"{prefix}{number:02d}",
                    connector_type=str(blueprint.get("connector_type", "RJ45")),
                    media_type=str(blueprint.get("media_type", "copper")),
                    direction=str(blueprint.get("direction", "bidirectional")),
                    front_or_rear=str(blueprint.get("face", "front")),
                    position_index=number,
                    position={"row": blueprint.get("row", 1), "column": number},
                    termination_type=blueprint.get("termination_type"),
                )
                self.session.add(port)
                self.session.flush()
                if blueprint.get("mapping_key"):
                    grouped.setdefault(str(blueprint["mapping_key"]), []).append(port)
        for ports in grouped.values():
            front = sorted(
                (p for p in ports if p.front_or_rear == "front"), key=lambda p: p.position_index
            )
            rear = sorted(
                (p for p in ports if p.front_or_rear == "rear"), key=lambda p: p.position_index
            )
            for source, target in zip(front, rear, strict=False):
                self.session.add(
                    PortMapping(
                        tenant_id=self.principal.tenant_id,
                        source_port_id=source.id,
                        target_port_id=target.id,
                        mapping_type="front_rear",
                    )
                )
        record_audit(
            self.session,
            principal=self.principal,
            action="device.created",
            object_type="device",
            object_id=device.id,
            after={"identifier": identifier, "template": str(template.id), "start_u": start_u},
        )
        return device

    def create_pathway(
        self,
        *,
        location_id: uuid.UUID,
        identifier: str,
        name: str,
        pathway_type: str,
        capacity_area_mm2: float | None = None,
        segments: list[dict[str, Any]] | None = None,
    ) -> Pathway:
        scope = self._authorization("pathway:create")
        scope.require(scope.get(Location, location_id), "pathway:create")
        pathway = Pathway(
            tenant_id=self.principal.tenant_id,
            location_id=location_id,
            identifier=identifier,
            name=name,
            pathway_type=pathway_type,
            capacity_area_mm2=capacity_area_mm2,
        )
        self.session.add(pathway)
        self.session.flush()
        for index, segment in enumerate(segments or [], start=1):
            self.session.add(
                PathwaySegment(
                    tenant_id=self.principal.tenant_id,
                    pathway_id=pathway.id,
                    sequence=int(segment.get("sequence", index)),
                    name=str(segment["name"]),
                    length_m=float(segment.get("length_m", 0)),
                    capacity_area_mm2=segment.get("capacity_area_mm2"),
                    reserved_percent=float(segment.get("reserved_percent", 0)),
                    coordinates=segment.get("coordinates", []),
                )
            )
        record_audit(
            self.session,
            principal=self.principal,
            action="pathway.created",
            object_type="pathway",
            object_id=pathway.id,
            after={"identifier": identifier, "segments": len(segments or [])},
        )
        return pathway

    def rack_elevation(self, rack_id: uuid.UUID) -> dict[str, Any]:
        scope = self._authorization("rack:read")
        rack = scope.get(Rack, rack_id)
        scope.get(Location, rack.location_id)
        scope.require(rack, "rack:read")
        devices = scope.visible(
            self.session.scalars(
                select(Device)
                .where(
                    Device.rack_id == rack.id,
                    Device.tenant_id == self.principal.tenant_id,
                    Device.deleted_at.is_(None),
                )
                .order_by(Device.start_u.desc())
            ),
            "rack:read",
        )
        device_ids = [d.id for d in devices]
        port_counts = {device_id: 0 for device_id in device_ids}
        if device_ids:
            port_counts.update(
                dict(
                    self.session.execute(
                        select(Port.device_id, func.count(Port.id))
                        .where(
                            Port.device_id.in_(device_ids),
                            Port.tenant_id == self.principal.tenant_id,
                            Port.deleted_at.is_(None),
                        )
                        .group_by(Port.device_id)
                    ).all()
                )
            )
        elevation = [
            ElevationDevice(
                id=d.id,
                identifier=d.identifier,
                name=d.name,
                device_type=d.device_type,
                start_u=d.start_u,
                rack_units=d.rack_units,
                face=d.face,
                ports=port_counts[d.id],
            )
            for d in devices
        ]
        used = {u for d in devices for u in range(d.start_u, d.start_u + d.rack_units)}
        reserved = set(rack.reserved_units)
        return {
            "rack": {
                "id": str(rack.id),
                "identifier": rack.rack_identifier,
                "name": rack.name,
                "height_u": rack.height_u,
                "width_mm": rack.width_mm,
                "depth_mm": rack.depth_mm,
                "position": {
                    "x": rack.position_x,
                    "y": rack.position_y,
                    "z": rack.position_z,
                    "rotation": rack.rotation,
                },
            },
            "devices": [{**asdict(item), "id": str(item.id)} for item in elevation],
            "reserved_units": sorted(reserved),
            "capacity": {
                "used_u": len(used),
                "reserved_u": len(reserved - used),
                "free_u": rack.height_u - len(used | reserved),
                "utilization_percent": round(len(used) / rack.height_u * 100, 2),
            },
        }

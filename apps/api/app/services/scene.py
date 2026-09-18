"""Bounded, explicitly scoped spatial inventory without per-cable trace queries."""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.exceptions import AuthorizationError, NotFoundError
from app.fiber_models import PhysicalPortClaim
from app.models import (
    Cable,
    CableRouteSegment,
    CableTermination,
    Device,
    DeviceTemplate,
    Location,
    LocationType,
    Pathway,
    PathwaySegment,
    Port,
    Project,
    Rack,
)
from app.schemas import CableRead, DeviceRead, LocationRead, PortRead, RackRead
from app.security import Principal, is_descendant_or_self, require_permission, resolve_principal
from app.services.cable_policy import cable_policy
from app.services.route_geometry import saved_portion_geometry


class SceneService:
    MAX_LOCATIONS = 1000
    MAX_RACKS = 300
    MAX_DEVICES = 5000
    MAX_PORTS = 10000
    MAX_PATHWAYS = 1000
    MAX_SEGMENTS = 5000
    MAX_CABLES = 5000
    MAX_COORDINATES = 512

    def __init__(self, db: Session, principal: Principal):
        self.db = db
        self.principal = principal
        self.truncated: list[str] = []

    def _query(self, model):
        # Explicit guards also protect calls from sessions without tenant hooks.
        return select(model).where(
            model.tenant_id == self.principal.tenant_id,
            model.deleted_at.is_(None),
        )

    def _rows(self, name: str, statement, limit: int):
        rows = list(self.db.scalars(statement.limit(limit + 1)).all())
        if len(rows) > limit:
            if name not in self.truncated:
                self.truncated.append(name)
            return rows[:limit]
        return rows

    def _authorize(self, location_id: uuid.UUID | None, project_id: uuid.UUID | None) -> Principal:
        context = self.principal
        # Services can outlive a request; neither cached membership nor caller-supplied
        # permissions may survive revocation, downgrade or a change of workspace owner.
        context = resolve_principal(
            self.db,
            actor_id=context.actor_id,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            location_id=context.location_id,
            request_id=context.request_id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
        )
        self.principal = context
        if context.project_id and project_id != context.project_id:
            raise AuthorizationError("Scene project is outside the selected context")
        if context.location_id and (
            location_id is None
            or not is_descendant_or_self(self.db, location_id, context.location_id)
        ):
            raise AuthorizationError("Scene location is outside the selected context")
        for model, object_id in ((Project, project_id), (Location, location_id)):
            if (
                object_id is not None
                and self.db.scalar(self._query(model).where(model.id == object_id)) is None
            ):
                raise NotFoundError(f"Scene {model.__name__.lower()} not found in tenant")
        principal = context
        if (location_id, project_id) != (context.location_id, context.project_id):
            # Evaluate permissions at the target without narrowing the selected context
            # kept by this service for subsequent operations on sibling locations.
            principal = resolve_principal(
                self.db,
                actor_id=context.actor_id,
                tenant_id=context.tenant_id,
                project_id=project_id,
                location_id=location_id,
                request_id=context.request_id,
                ip_address=context.ip_address,
                user_agent=context.user_agent,
            )
        for permission in (
            "location:read",
            "rack:read",
            "device:read",
            "port:read",
            "pathway:read",
            "cable:read",
        ):
            require_permission(principal, permission)
        return principal

    def _locations(self, location_id: uuid.UUID | None):
        if location_id is None:
            return self._rows(
                "locations",
                self._query(Location).order_by(Location.identifier, Location.id),
                self.MAX_LOCATIONS,
            )
        root = self.db.scalar(
            self._query(Location)
            .where(Location.id == location_id)
            .execution_options(populate_existing=True)
        )
        if root is None:
            raise NotFoundError("Scene location not found in tenant")
        bounded_space = root.location_type in {
            LocationType.FLOOR,
            LocationType.ZONE,
            LocationType.ROOM,
            LocationType.TR,
            LocationType.ER,
            LocationType.MDF,
            LocationType.MMR,
            LocationType.DATA_HALL,
            LocationType.ENTRANCE_FACILITY,
            LocationType.ROW,
        }
        rows, frontier, visited = [root], [root.id], {root.id}
        while frontier:
            query = self._query(Location).where(
                Location.parent_id.in_(frontier), Location.id.not_in(visited)
            )
            if bounded_space:
                # Even malformed hierarchies cannot mix another floor/building into
                # a selected physical space. Broad directory reads remain compatible.
                query = query.where(
                    Location.location_type.not_in([LocationType.FLOOR, LocationType.BUILDING])
                )
            children = self._rows(
                "locations",
                query.order_by(Location.identifier, Location.id),
                self.MAX_LOCATIONS - len(rows),
            )
            rows.extend(children)
            frontier = [row.id for row in children]
            visited.update(frontier)
        return rows

    def _port_page(self, device_ids, limit: int, after: uuid.UUID | None = None):
        query = self._query(Port).where(Port.device_id.in_(device_ids))
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        if after is not None:
            query = query.where(Port.id > after)
        rows = list(self.db.scalars(query.order_by(Port.id).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return rows, {"total": total, "next_cursor": str(rows[-1].id) if more else None}

    def _port_records(self, ports):
        ids = [row.id for row in ports]
        occupied = set(
            self.db.scalars(
                select(PhysicalPortClaim.port_id).where(
                    PhysicalPortClaim.tenant_id == self.principal.tenant_id,
                    PhysicalPortClaim.deleted_at.is_(None),
                    PhysicalPortClaim.port_id.in_(ids),
                )
            )
        )
        occupied.update(
            self.db.scalars(
                select(CableTermination.port_id).where(
                    CableTermination.tenant_id == self.principal.tenant_id,
                    CableTermination.deleted_at.is_(None),
                    CableTermination.port_id.in_(ids),
                )
            )
        )
        return [
            {**PortRead.model_validate(row).model_dump(mode="json"), "occupied": row.id in occupied}
            for row in ports
        ]

    def read_ports(self, *, location_id=None, project_id=None, after=None, limit=10000):
        self.truncated = []
        location_id = location_id or self.principal.location_id
        project_id = project_id or self.principal.project_id
        self._authorize(location_id, project_id)
        locations = self._locations(location_id)
        # Match the scene's bounded inventory exactly; a cursor never widens scope.
        racks = self._rows(
            "racks",
            self._query(Rack)
            .where(Rack.location_id.in_([row.id for row in locations]))
            .order_by(Rack.rack_identifier, Rack.id),
            self.MAX_RACKS,
        )
        devices = self._rows(
            "devices",
            self._query(Device)
            .where(
                Device.location_id.in_([row.id for row in locations]),
                or_(Device.rack_id.is_(None), Device.rack_id.in_([row.id for row in racks])),
            )
            .order_by(Device.identifier, Device.id),
            self.MAX_DEVICES,
        )
        ports, page = self._port_page([row.id for row in devices], limit, after)
        return {
            "scope": {
                "tenant_id": str(self.principal.tenant_id),
                "project_id": str(project_id) if project_id else None,
                "location_id": str(location_id) if location_id else None,
            },
            "ports": self._port_records(ports),
            "port_page": page,
            "truncated": self.truncated,
        }

    def read(
        self,
        *,
        location_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        self.truncated = []
        location_id = location_id or self.principal.location_id
        project_id = project_id or self.principal.project_id
        self._authorize(location_id, project_id)
        locations = self._locations(location_id)
        location_ids = [row.id for row in locations]
        racks = self._rows(
            "racks",
            self._query(Rack)
            .where(Rack.location_id.in_(location_ids))
            .order_by(Rack.rack_identifier, Rack.id),
            self.MAX_RACKS,
        )
        rack_ids = {row.id for row in racks}
        devices = self._rows(
            "devices",
            self._query(Device)
            .where(
                Device.location_id.in_(location_ids),
                or_(Device.rack_id.is_(None), Device.rack_id.in_(rack_ids)),
            )
            .order_by(Device.identifier, Device.id),
            self.MAX_DEVICES,
        )
        device_by_id = {row.id: row for row in devices}
        templates = self.db.scalars(
            self._query(DeviceTemplate).where(
                DeviceTemplate.id.in_({row.template_id for row in devices if row.template_id}),
            )
        ).all()
        template_by_id = {row.id: row for row in templates}
        ports, port_page = self._port_page(device_by_id, self.MAX_PORTS)
        if port_page["next_cursor"]:
            self.truncated.append("ports")
        # Cable discovery and endpoint scope use all authorized ports, not just page one.
        visible_port_ids = (
            self._query(Port).with_only_columns(Port.id).where(Port.device_id.in_(device_by_id))
        )
        pathways = self._rows(
            "pathways",
            self._query(Pathway)
            .where(Pathway.location_id.in_(location_ids))
            .order_by(Pathway.identifier, Pathway.id),
            self.MAX_PATHWAYS,
        )
        segments = self._rows(
            "segments",
            self._query(PathwaySegment)
            .where(PathwaySegment.pathway_id.in_([row.id for row in pathways]))
            .order_by(PathwaySegment.pathway_id, PathwaySegment.sequence, PathwaySegment.id),
            self.MAX_SEGMENTS,
        )
        segment_ids = {row.id for row in segments}
        visible_term_cables = select(CableTermination.cable_id).where(
            CableTermination.tenant_id == self.principal.tenant_id,
            CableTermination.deleted_at.is_(None),
            CableTermination.port_id.in_(visible_port_ids),
        )
        visible_route_cables = select(CableRouteSegment.cable_id).where(
            CableRouteSegment.tenant_id == self.principal.tenant_id,
            CableRouteSegment.deleted_at.is_(None),
            CableRouteSegment.pathway_segment_id.in_(segment_ids),
        )
        cable_query = self._query(Cable).where(
            or_(
                Cable.id.in_(visible_term_cables),
                Cable.id.in_(visible_route_cables),
            )
        )
        if project_id:
            cable_query = cable_query.where(Cable.project_id == project_id)
        cables = self._rows(
            "cables",
            cable_query.order_by(Cable.identifier, Cable.id),
            self.MAX_CABLES,
        )
        cable_ids = [row.id for row in cables]
        terms = self._rows(
            "terminations",
            self._query(CableTermination)
            .where(
                CableTermination.cable_id.in_(cable_ids),
                CableTermination.port_id.in_(visible_port_ids),
            )
            .order_by(CableTermination.cable_id, CableTermination.side),
            self.MAX_CABLES * 2,
        )
        routes = self._rows(
            "routes",
            self._query(CableRouteSegment)
            .where(
                CableRouteSegment.cable_id.in_(cable_ids),
                CableRouteSegment.pathway_segment_id.in_(segment_ids),
            )
            .order_by(CableRouteSegment.cable_id, CableRouteSegment.sequence),
            self.MAX_SEGMENTS * 4,
        )
        # Count registered route rows independently of the visible segment catalog.
        # Only completeness is exposed, never the identifiers of omitted segments.
        route_counts = (
            dict(
                self.db.execute(
                    select(CableRouteSegment.cable_id, func.count(CableRouteSegment.id))
                    .where(
                        CableRouteSegment.tenant_id == self.principal.tenant_id,
                        CableRouteSegment.deleted_at.is_(None),
                        CableRouteSegment.cable_id.in_(cable_ids),
                    )
                    .group_by(CableRouteSegment.cable_id)
                ).all()
            )
            if cable_ids
            else {}
        )
        term_ports = {
            row.id: row
            for row in self.db.scalars(
                self._query(Port).where(Port.id.in_([term.port_id for term in terms]))
            )
        }
        term_by_cable: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
        for term in terms:
            port = term_ports[term.port_id]
            device = device_by_id[port.device_id]
            term_by_cable[term.cable_id].append(
                {
                    "side": term.side,
                    "port_id": str(port.id),
                    "device_id": str(device.id),
                    "rack_id": str(device.rack_id) if device.rack_id else None,
                    "location_id": str(device.location_id),
                }
            )
        route_by_cable: dict[uuid.UUID, list[str]] = defaultdict(list)
        portions_by_cable: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
        segment_lookup = {segment.id: segment for segment in segments}
        for route in routes:
            route_by_cable[route.cable_id].append(str(route.pathway_segment_id))
            if route.start_offset_m is not None:
                valid = (
                    saved_portion_geometry(segment_lookup[route.pathway_segment_id], route)
                    is not None
                )
                portions_by_cable[route.cable_id].append(
                    {
                        "segment_id": str(route.pathway_segment_id),
                        "start_offset_m": route.start_offset_m,
                        "end_offset_m": route.end_offset_m,
                        "geometry_hash": route.geometry_hash,
                        "valid": valid,
                    }
                )
        segment_by_pathway: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
        for segment in segments:
            if (
                len(segment.coordinates) > self.MAX_COORDINATES
                and "coordinates" not in self.truncated
            ):
                self.truncated.append("coordinates")
            segment_by_pathway[segment.pathway_id].append(
                {
                    "id": str(segment.id),
                    "sequence": segment.sequence,
                    "name": segment.name,
                    "length_m": segment.length_m,
                    "coordinates": segment.coordinates[: self.MAX_COORDINATES],
                }
            )
        return {
            "scope": {
                "tenant_id": str(self.principal.tenant_id),
                "project_id": str(project_id) if project_id else None,
                "location_id": str(location_id) if location_id else None,
                "include_descendants": True,
            },
            "units": {
                "positions": "m",
                "rack_dimensions": "mm",
                "rack_rotation": "degrees",
                "vertical_axis": "z",
            },
            "locations": [
                LocationRead.model_validate(row).model_dump(mode="json") for row in locations
            ],
            "racks": [
                {
                    **RackRead.model_validate(row).model_dump(mode="json"),
                    "status": row.status,
                    "front_direction": row.front_direction,
                    "role": row.role,
                }
                for row in racks
            ],
            "devices": [
                {
                    **DeviceRead.model_validate(row).model_dump(mode="json"),
                    "status": row.status,
                    "cable_policy": cable_policy(row.instance_overrides.get("cable_policy")),
                    "width_mm": template_by_id[row.template_id].width_mm
                    if row.template_id in template_by_id
                    else None,
                    "depth_mm": template_by_id[row.template_id].depth_mm
                    if row.template_id in template_by_id
                    else None,
                }
                for row in devices
            ],
            "ports": self._port_records(ports),
            "port_page": port_page,
            "pathways": [
                {
                    "id": str(row.id),
                    "location_id": str(row.location_id),
                    "identifier": row.identifier,
                    "name": row.name,
                    "type": row.pathway_type,
                    "status": row.status,
                    "version": row.version,
                    "cable_policy": cable_policy(row.cable_policy),
                    "capacity_area_mm2": row.capacity_area_mm2,
                    "segments": segment_by_pathway[row.id],
                }
                for row in pathways
            ],
            "cables": [
                {
                    **CableRead.model_validate(row).model_dump(mode="json"),
                    "terminations": term_by_cable[row.id],
                    "route_segment_ids": route_by_cable[row.id],
                    **(
                        {"route_portions": portions_by_cable[row.id]}
                        if portions_by_cable[row.id]
                        else {}
                    ),
                    "route_scope": "none"
                    if not route_counts.get(row.id)
                    else "complete"
                    if len(route_by_cable[row.id]) == route_counts[row.id]
                    else "partial",
                    "endpoint_scope": "complete"
                    if len(term_by_cable[row.id]) == 2
                    else "partial"
                    if term_by_cable[row.id]
                    else "none",
                }
                for row in cables
            ],
            "truncated": self.truncated,
        }

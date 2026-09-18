from __future__ import annotations

import csv
import io
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.exceptions import AuthorizationError
from app.models import (
    AccessGrant,
    AccessGrantStatus,
    Cable,
    CableRouteSegment,
    CableStatus,
    CableTermination,
    Device,
    Location,
    LocationType,
    Pathway,
    PathwaySegment,
    Port,
    Project,
    Rack,
    TestRecord,
    WorkOrder,
    WorkOrderStatus,
)
from app.security import Principal, require_permission
from app.services.resource_scope import ResourceScope
from app.services.route_geometry import saved_portion_geometry


CABLE_SCHEDULE_FIELDS: tuple[tuple[str, str], ...] = (
    ("cable_id", "Cable ID"),
    ("cable_identifier", "Cable Identifier"),
    ("project_number", "Project Number"),
    ("project_name", "Project Name"),
    ("installation_status", "Installation Status"),
    ("media_type", "Media Type"),
    ("construction", "Construction"),
    ("manufacturer", "Manufacturer"),
    ("part_number", "Part Number"),
    ("color", "Color"),
    ("design_length_m", "Design Length (m)"),
    ("measured_length_m", "Measured Length (m)"),
    ("strand_count", "Strand Count"),
    ("pair_count", "Pair Count"),
    ("test_status", "Test Status"),
    ("installed_at", "Installed At"),
    ("tested_at", "Tested At"),
    ("a_location", "A Location Path"),
    ("a_device", "A Device"),
    ("a_device_name", "A Device Name"),
    ("a_port", "A Port"),
    ("a_port_label", "A Port Label"),
    ("a_connector", "A Connector"),
    ("a_port_media", "A Port Media"),
    ("a_strand", "A Strand"),
    ("a_pair", "A Pair"),
    ("b_location", "B Location Path"),
    ("b_device", "B Device"),
    ("b_device_name", "B Device Name"),
    ("b_port", "B Port"),
    ("b_port_label", "B Port Label"),
    ("b_connector", "B Connector"),
    ("b_port_media", "B Port Media"),
    ("b_strand", "B Strand"),
    ("b_pair", "B Pair"),
    ("pathway_route", "Pathway Route"),
    ("route_length_m", "Route Length (m)"),
)


@dataclass(frozen=True)
class CableScheduleExport:
    filename: str
    content: bytes
    row_count: int
    total_count: int
    truncated: bool
    filters: dict[str, str | int | None]


def _spreadsheet_safe(value: Any) -> Any:
    """Neutralize spreadsheet formulas without converting numeric fields to strings."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str):
        normalized = value.lstrip()
        if normalized.startswith(("=", "+", "-", "@", "\t", "\r")):
            return f"'{value}"
    return value


class ReportingService:
    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.scope = ResourceScope(session, principal)
        self.principal = self.scope.principal

    def _select(self, model):
        return select(model).where(
            model.tenant_id == self.principal.tenant_id,
            model.deleted_at.is_(None),
        )

    def _authorize(self, permission):
        self.scope = ResourceScope(self.session, self.principal)
        self.principal = self.scope.principal
        require_permission(self.principal, permission)

    def dashboard(self) -> dict[str, Any]:
        self._authorize("dashboard:read")

        def count(model, *conditions):
            if self.principal.is_tenant_member:
                return (
                    self.session.scalar(
                        select(func.count(model.id)).where(
                            model.tenant_id == self.principal.tenant_id,
                            model.deleted_at.is_(None),
                            *conditions,
                        )
                    )
                    or 0
                )
            return sum(
                1
                for _ in self.scope.iter_visible(
                    self.session.scalars(self._select(model).where(*conditions)),
                    "dashboard:read",
                )
            )

        grant_query = select(func.count(AccessGrant.id)).where(
            AccessGrant.tenant_id == self.principal.tenant_id,
            AccessGrant.deleted_at.is_(None),
            AccessGrant.status == AccessGrantStatus.ACTIVE,
        )
        if not self.principal.is_tenant_member:
            grant_query = grant_query.where(AccessGrant.id.in_(self.principal.grant_ids))
        counts = {
            "buildings": count(Location, Location.location_type == LocationType.BUILDING),
            "telecom_rooms": count(
                Location,
                Location.location_type.in_(
                    [LocationType.TR, LocationType.MDF, LocationType.ER, LocationType.MMR]
                ),
            ),
            "racks": count(Rack),
            "devices": count(Device),
            "active_cables": count(Cable, Cable.installation_status != CableStatus.REMOVED),
            "open_work_orders": count(
                WorkOrder,
                WorkOrder.status.not_in([WorkOrderStatus.COMPLETED, WorkOrderStatus.CANCELLED]),
            ),
            "failed_tests": count(TestRecord, TestRecord.result == "FAIL"),
            "expiring_access": self.session.scalar(grant_query) or 0,
        }
        recent_query = self._select(WorkOrder).order_by(WorkOrder.created_at.desc())
        if self.principal.is_tenant_member:
            recent_query = recent_query.limit(5)
        recent = islice(
            self.scope.iter_visible(self.session.scalars(recent_query), "dashboard:read"), 5
        )
        return {
            "counts": counts,
            "recent_work_orders": [
                {
                    "id": str(item.id),
                    "number": item.work_order_number,
                    "title": item.title,
                    "status": item.status.value,
                    "due_at": item.due_at.isoformat() if item.due_at else None,
                }
                for item in recent
            ],
        }

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        self._authorize("search:read")
        normalized = query.strip()
        if not normalized:
            return []
        like = f"%{normalized}%"
        results: list[dict[str, Any]] = []
        sources = [
            ("location", Location, Location.identifier, Location.name),
            ("rack", Rack, Rack.rack_identifier, Rack.name),
            ("device", Device, Device.identifier, Device.name),
            ("cable", Cable, Cable.identifier, Cable.identifier),
            ("work_order", WorkOrder, WorkOrder.work_order_number, WorkOrder.title),
        ]
        for resource_type, model, identifier_column, name_column in sources:
            statement = (
                self._select(model)
                .where(or_(identifier_column.ilike(like), name_column.ilike(like)))
                .order_by(identifier_column)
            )
            if self.principal.is_tenant_member:
                statement = statement.limit(limit)
            rows = islice(
                self.scope.iter_visible(self.session.scalars(statement), "search:read"), limit
            )
            for row in rows:
                identifier = getattr(row, identifier_column.key)
                name = getattr(row, name_column.key)
                results.append(
                    {
                        "type": resource_type,
                        "id": str(row.id),
                        "identifier": identifier,
                        "name": name,
                        "exact": identifier.lower() == normalized.lower(),
                    }
                )
        results.sort(key=lambda item: (not item["exact"], item["type"], item["identifier"]))
        return results[:limit]

    def export_cable_schedule(
        self,
        *,
        status: CableStatus | None = None,
        project_id: uuid.UUID | None = None,
        query: str | None = None,
        limit: int = 10_000,
    ) -> CableScheduleExport:
        self._authorize("report:export")
        if not self.principal.is_tenant_member:
            raise AuthorizationError("Cable schedule export is limited to tenant members")
        normalized_query = query.strip() if query and query.strip() else None
        cable_filters = [Cable.tenant_id == self.principal.tenant_id, Cable.deleted_at.is_(None)]
        if project_id is not None:
            self.scope.get(Project, project_id)
        if status is not None:
            cable_filters.append(Cable.installation_status == status)
        if project_id is not None:
            cable_filters.append(Cable.project_id == project_id)
        if normalized_query:
            like = f"%{normalized_query}%"
            cable_filters.append(
                or_(
                    Cable.identifier.ilike(like),
                    Cable.media_type.ilike(like),
                    Cable.construction.ilike(like),
                    Cable.manufacturer.ilike(like),
                    Cable.part_number.ilike(like),
                )
            )
        count_statement = select(func.count(Cable.id))
        cable_statement = select(Cable).order_by(Cable.identifier).limit(limit)
        if cable_filters:
            count_statement = count_statement.where(*cable_filters)
            cable_statement = cable_statement.where(*cable_filters)
        total_count = self.session.scalar(count_statement) or 0
        cables = self.session.scalars(cable_statement).all()
        cable_ids = [cable.id for cable in cables]
        projects = self._projects_for(cables)
        terminations_by_cable, ports, devices = self._endpoints_for(cable_ids)
        locations = self._location_ancestors({device.location_id for device in devices.values()})
        route_items_by_cable, pathway_segments, pathways = self._routes_for(cable_ids)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output,
            fieldnames=[field for field, _label in CABLE_SCHEDULE_FIELDS],
            lineterminator="\r\n",
            extrasaction="ignore",
        )
        writer.writerow({field: label for field, label in CABLE_SCHEDULE_FIELDS})
        for cable in cables:
            project = projects.get(cable.project_id) if cable.project_id else None
            route_labels: list[str] = []
            route_length_m = 0.0
            route_valid = True
            for route_item in route_items_by_cable.get(cable.id, []):
                segment = pathway_segments.get(route_item.pathway_segment_id)
                pathway = pathways.get(segment.pathway_id) if segment else None
                if not segment:
                    route_valid = False
                    continue
                label = f"{pathway.identifier}:{segment.name}" if pathway else segment.name
                length = segment.length_m
                if route_item.start_offset_m is not None:
                    geometry = saved_portion_geometry(segment, route_item)
                    label += (
                        f" [{route_item.start_offset_m:.3f} -> {route_item.end_offset_m:.3f} m]"
                    )
                    if geometry:
                        length = geometry.adopted_length(
                            route_item.start_offset_m, route_item.end_offset_m
                        )
                    else:
                        label += " (geometry changed; review required)"
                        route_valid = False
                        length = 0
                route_labels.append(label)
                route_length_m += length
            row: dict[str, Any] = {
                "cable_id": cable.id,
                "cable_identifier": cable.identifier,
                "project_number": project.project_number if project else "",
                "project_name": project.name if project else "",
                "installation_status": cable.installation_status.value,
                "media_type": cable.media_type,
                "construction": cable.construction,
                "manufacturer": cable.manufacturer,
                "part_number": cable.part_number,
                "color": cable.color,
                "design_length_m": cable.length_m,
                "measured_length_m": cable.measured_length_m,
                "strand_count": cable.strand_count,
                "pair_count": cable.pair_count,
                "test_status": cable.test_status,
                "installed_at": cable.installed_at,
                "tested_at": cable.tested_at,
                "pathway_route": " > ".join(route_labels),
                "route_length_m": round(route_length_m, 3) if route_labels and route_valid else "",
            }
            endpoints = terminations_by_cable.get(cable.id, {})
            row.update(
                self._endpoint_fields(
                    endpoints.get("A"), "a", ports=ports, devices=devices, locations=locations
                )
            )
            row.update(
                self._endpoint_fields(
                    endpoints.get("B"), "b", ports=ports, devices=devices, locations=locations
                )
            )
            writer.writerow(
                {
                    field: _spreadsheet_safe(row.get(field))
                    for field, _label in CABLE_SCHEDULE_FIELDS
                }
            )
        filters: dict[str, str | int | None] = {
            "status": status.value if status else None,
            "project_id": str(project_id) if project_id else None,
            "query": normalized_query,
            "limit": limit,
        }
        row_count = len(cables)
        safe_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return CableScheduleExport(
            filename=f"cable-schedule-{safe_timestamp}.csv",
            content=output.getvalue().encode("utf-8-sig"),
            row_count=row_count,
            total_count=total_count,
            truncated=row_count < total_count,
            filters=filters,
        )

    def _projects_for(self, cables: list[Cable]) -> dict[uuid.UUID, Project]:
        project_ids = {cable.project_id for cable in cables if cable.project_id is not None}
        if not project_ids:
            return {}
        return {
            project.id: project
            for project in self.session.scalars(
                self._select(Project).where(Project.id.in_(project_ids))
            ).all()
        }

    def _endpoints_for(
        self, cable_ids: list[uuid.UUID]
    ) -> tuple[
        dict[uuid.UUID, dict[str, CableTermination]], dict[uuid.UUID, Port], dict[uuid.UUID, Device]
    ]:
        if not cable_ids:
            return {}, {}, {}
        terminations = self.session.scalars(
            self._select(CableTermination).where(CableTermination.cable_id.in_(cable_ids))
        ).all()
        by_cable: dict[uuid.UUID, dict[str, CableTermination]] = defaultdict(dict)
        for termination in terminations:
            by_cable[termination.cable_id][termination.side] = termination
        port_ids = {termination.port_id for termination in terminations}
        ports = (
            {
                port.id: port
                for port in self.session.scalars(
                    self._select(Port).where(Port.id.in_(port_ids))
                ).all()
            }
            if port_ids
            else {}
        )
        device_ids = {port.device_id for port in ports.values()}
        devices = (
            {
                device.id: device
                for device in self.session.scalars(
                    self._select(Device).where(Device.id.in_(device_ids))
                ).all()
            }
            if device_ids
            else {}
        )
        return dict(by_cable), ports, devices

    def _location_ancestors(self, location_ids: set[uuid.UUID]) -> dict[uuid.UUID, Location]:
        locations: dict[uuid.UUID, Location] = {}
        pending = set(location_ids)
        while pending:
            fetched = self.session.scalars(
                self._select(Location).where(Location.id.in_(pending))
            ).all()
            if not fetched:
                break
            pending = set()
            for location in fetched:
                locations[location.id] = location
                if location.parent_id and location.parent_id not in locations:
                    pending.add(location.parent_id)
        return locations

    def _routes_for(
        self, cable_ids: list[uuid.UUID]
    ) -> tuple[
        dict[uuid.UUID, list[CableRouteSegment]],
        dict[uuid.UUID, PathwaySegment],
        dict[uuid.UUID, Pathway],
    ]:
        if not cable_ids:
            return {}, {}, {}
        route_items = self.session.scalars(
            self._select(CableRouteSegment)
            .where(CableRouteSegment.cable_id.in_(cable_ids))
            .order_by(CableRouteSegment.cable_id, CableRouteSegment.sequence)
        ).all()
        by_cable: dict[uuid.UUID, list[CableRouteSegment]] = defaultdict(list)
        for route_item in route_items:
            by_cable[route_item.cable_id].append(route_item)
        segment_ids = {route_item.pathway_segment_id for route_item in route_items}
        segments = (
            {
                segment.id: segment
                for segment in self.session.scalars(
                    self._select(PathwaySegment).where(PathwaySegment.id.in_(segment_ids))
                ).all()
            }
            if segment_ids
            else {}
        )
        pathway_ids = {segment.pathway_id for segment in segments.values()}
        pathways = (
            {
                pathway.id: pathway
                for pathway in self.session.scalars(
                    self._select(Pathway).where(Pathway.id.in_(pathway_ids))
                ).all()
            }
            if pathway_ids
            else {}
        )
        return dict(by_cable), segments, pathways

    @staticmethod
    def _endpoint_fields(
        termination: CableTermination | None,
        prefix: str,
        *,
        ports: dict[uuid.UUID, Port],
        devices: dict[uuid.UUID, Device],
        locations: dict[uuid.UUID, Location],
    ) -> dict[str, Any]:
        empty = {
            f"{prefix}_location": "",
            f"{prefix}_device": "",
            f"{prefix}_device_name": "",
            f"{prefix}_port": "",
            f"{prefix}_port_label": "",
            f"{prefix}_connector": "",
            f"{prefix}_port_media": "",
            f"{prefix}_strand": "",
            f"{prefix}_pair": "",
        }
        if termination is None:
            return empty
        port = ports.get(termination.port_id)
        device = devices.get(port.device_id) if port else None
        if not port or not device:
            return empty
        path: list[str] = []
        current = locations.get(device.location_id)
        visited: set[uuid.UUID] = set()
        while current and current.id not in visited:
            path.append(current.identifier)
            visited.add(current.id)
            current = locations.get(current.parent_id) if current.parent_id else None
        path.reverse()
        return {
            f"{prefix}_location": " / ".join(path),
            f"{prefix}_device": device.identifier,
            f"{prefix}_device_name": device.name,
            f"{prefix}_port": port.identifier,
            f"{prefix}_port_label": port.label,
            f"{prefix}_connector": port.connector_type,
            f"{prefix}_port_media": port.media_type,
            f"{prefix}_strand": termination.strand,
            f"{prefix}_pair": termination.pair,
        }

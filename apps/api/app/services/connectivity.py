from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update, or_, select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.fiber_models import PhysicalPortClaim
from app.models import (
    Cable,
    CableRouteSegment,
    CableTermination,
    Device,
    Location,
    Pathway,
    PathwaySegment,
    Port,
    PortMapping,
    Project,
    Rack,
)
from app.security import Principal, require_permission
from app.services.cable_policy import media_family, require_cable_admission
from app.services.resource_scope import ResourceScope
from app.services.route_geometry import saved_portion_geometry


@dataclass(frozen=True)
class Edge:
    neighbor: uuid.UUID
    kind: str
    resource_id: uuid.UUID


class ConnectivityService:
    MAX_TRACE_NODES = 500

    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.principal = principal

    _media_family = staticmethod(media_family)

    def _query(self, model):
        return select(model).where(
            model.tenant_id == self.principal.tenant_id, model.deleted_at.is_(None)
        )

    def _lock_policy_rows(self, model, ids):
        ids = sorted(set(ids), key=str)
        conditions = (
            model.id.in_(ids),
            model.tenant_id == self.principal.tenant_id,
            model.deleted_at.is_(None),
        )
        if self.session.get_bind().dialect.name == "sqlite":
            self.session.execute(
                update(model)
                .where(*conditions)
                .values(version=model.version, updated_at=model.updated_at)
                .execution_options(synchronize_session=False)
            )
        return self.session.scalars(
            select(model)
            .where(*conditions)
            .order_by(model.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()

    def _validate_ports_for_cable(self, cable_media: str, port_a: Port, port_b: Port) -> None:
        if port_a.id == port_b.id:
            raise ValidationError("A cable cannot terminate twice on the same port")
        cable_family = self._media_family(cable_media)
        for port in (port_a, port_b):
            if self._media_family(port.media_type) != cable_family:
                raise ValidationError(
                    f"Cable media {cable_media} is incompatible with port {port.identifier} "
                    f"media {port.media_type}"
                )
        port_ids = [port_a.id, port_b.id]
        occupied = self.session.scalar(
            select(PhysicalPortClaim.id).where(
                PhysicalPortClaim.port_id.in_(port_ids),
                PhysicalPortClaim.deleted_at.is_(None),
            )
        )
        # The fallback protects databases still being repaired/backfilled and makes
        # the boundary safe even if a legacy row was inserted outside this service.
        if occupied is None:
            occupied = self.session.scalar(
                select(CableTermination.id).where(
                    CableTermination.port_id.in_(port_ids),
                    CableTermination.deleted_at.is_(None),
                )
            )
        if occupied:
            raise ConflictError("One or more ports are already physically terminated")

    def create_cable(
        self,
        *,
        identifier: str,
        media_type: str,
        construction: str,
        port_a_id: uuid.UUID,
        port_b_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        color: str | None = None,
        length_m: float | None = None,
        route_segment_ids: list[uuid.UUID] | None = None,
        route_portions: list[dict] | None = None,
    ) -> Cable:
        scope = ResourceScope(self.session, self.principal)
        require_permission(scope.principal, "cable:create")
        if project_id is not None:
            scope.get(Project, project_id)
        elif not scope.principal.is_tenant_member:
            raise AuthorizationError("Scoped cable creation requires an actual project")
        port_a = scope.get(Port, port_a_id)
        port_b = scope.get(Port, port_b_id)
        for port in (port_a, port_b):
            device = scope.get(Device, port.device_id)
            scope.require_location("cable:create", project_id, device.location_id)
        devices = self._lock_policy_rows(Device, [port_a.device_id, port_b.device_id])
        if {row.id for row in devices} != {port_a.device_id, port_b.device_id}:
            raise NotFoundError("Endpoint device not found in tenant")
        for device in devices:
            require_cable_admission(
                device.instance_overrides.get("cable_policy"), media_type, "Device"
            )
        route_segments = self.session.scalars(
            select(PathwaySegment).where(
                PathwaySegment.id.in_(route_segment_ids or []),
                PathwaySegment.tenant_id == self.principal.tenant_id,
                PathwaySegment.deleted_at.is_(None),
            )
        ).all()
        if {row.id for row in route_segments} != set(route_segment_ids or []):
            raise NotFoundError("Pathway segment not found in tenant")
        for segment in route_segments:
            pathway = scope.get(Pathway, segment.pathway_id)
            scope.require_location("cable:create", project_id, pathway.location_id)
        pathways = self._lock_policy_rows(Pathway, [row.pathway_id for row in route_segments])
        if {row.id for row in pathways} != {row.pathway_id for row in route_segments}:
            raise NotFoundError("Pathway not found in tenant")
        for pathway in pathways:
            require_cable_admission(pathway.cable_policy, media_type, "Pathway")
        self.session.flush()
        self.session.expire_all()
        scope = ResourceScope(self.session, self.principal)
        # Locks serialize policy changes; recheck current references and grants
        # after waiting rather than relying on the caller's earlier header scope.
        for port_id in (port_a_id, port_b_id):
            port = scope.get(Port, port_id)
            device = scope.get(Device, port.device_id)
            scope.require_location("cable:create", project_id, device.location_id)
            require_cable_admission(
                device.instance_overrides.get("cable_policy"), media_type, "Device"
            )
        for segment_id in route_segment_ids or []:
            segment = scope.get(PathwaySegment, segment_id)
            pathway = scope.get(Pathway, segment.pathway_id)
            scope.require_location("cable:create", project_id, pathway.location_id)
            require_cable_admission(pathway.cable_policy, media_type, "Pathway")
        validated_portions = []
        if route_portions is not None:
            from app.services.route_geometry import validate_portions

            if len(set(route_segment_ids or [])) != len(route_segment_ids or []):
                raise ValidationError("Route portions require distinct ordered segments")
            locations = {
                scope.get(Device, scope.get(Port, id).device_id).location_id
                for id in (port_a_id, port_b_id)
            }
            locations.update(
                scope.get(Pathway, scope.get(PathwaySegment, id).pathway_id).location_id
                for id in route_segment_ids or []
            )
            if route_portions and len(locations) != 1:
                raise ValidationError("Partial tray routing requires one room coordinate frame")
            validated_portions = validate_portions(
                [scope.get(PathwaySegment, id) for id in route_segment_ids or []], route_portions
            )
        self._validate_ports_for_cable(media_type, port_a, port_b)
        cable = Cable(
            tenant_id=self.principal.tenant_id,
            project_id=project_id,
            identifier=identifier,
            media_type=media_type,
            construction=construction,
            color=color,
            length_m=length_m,
        )
        self.session.add(cable)
        self.session.flush()
        termination_a = CableTermination(
            tenant_id=self.principal.tenant_id,
            cable_id=cable.id,
            side="A",
            port_id=port_a.id,
        )
        termination_b = CableTermination(
            tenant_id=self.principal.tenant_id,
            cable_id=cable.id,
            side="B",
            port_id=port_b.id,
        )
        self.session.add_all([termination_a, termination_b])
        self.session.flush()
        self.session.add_all(
            [
                PhysicalPortClaim(
                    tenant_id=self.principal.tenant_id,
                    port_id=port_a.id,
                    owner_type="cable_termination",
                    owner_id=termination_a.id,
                ),
                PhysicalPortClaim(
                    tenant_id=self.principal.tenant_id,
                    port_id=port_b.id,
                    owner_type="cable_termination",
                    owner_id=termination_b.id,
                ),
            ]
        )
        self.session.flush()
        for sequence, segment_id in enumerate(route_segment_ids or [], start=1):
            if not self.session.get(PathwaySegment, segment_id):
                raise NotFoundError("Pathway segment not found in tenant")
            self.session.add(
                CableRouteSegment(
                    tenant_id=self.principal.tenant_id,
                    cable_id=cable.id,
                    pathway_segment_id=segment_id,
                    sequence=sequence,
                    **(
                        {
                            key: value
                            for key, value in validated_portions[sequence - 1].items()
                            if key != "segment_id"
                        }
                        if validated_portions
                        else {}
                    ),
                )
            )
        record_audit(
            self.session,
            principal=self.principal,
            action="cable.created",
            object_type="cable",
            object_id=cable.id,
            after={
                "identifier": identifier,
                "port_a": str(port_a.id),
                "port_b": str(port_b.id),
                **(
                    {
                        "route_portions": [
                            {**part, "segment_id": str(part["segment_id"])}
                            for part in validated_portions
                        ]
                    }
                    if validated_portions
                    else {}
                ),
            },
            project_id=project_id,
        )
        return cable

    def _expand_graph(
        self, seed_ports: list[uuid.UUID], scope: ResourceScope
    ) -> tuple[dict[uuid.UUID, list[Edge]], dict[uuid.UUID, Cable], dict[uuid.UUID, PortMapping]]:
        adjacency: dict[uuid.UUID, list[Edge]] = defaultdict(list)
        cables: dict[uuid.UUID, Cable] = {}
        mappings: dict[uuid.UUID, PortMapping] = {}
        visited: set[uuid.UUID] = set()
        frontier: deque[uuid.UUID] = deque(seed_ports)
        expanded_cables: set[uuid.UUID] = set()
        expanded_mappings: set[uuid.UUID] = set()

        while frontier:
            batch: list[uuid.UUID] = []
            while frontier and len(batch) < 100:
                port_id = frontier.popleft()
                if port_id not in visited:
                    visited.add(port_id)
                    batch.append(port_id)
            if not batch:
                continue
            if len(visited) > self.MAX_TRACE_NODES:
                raise ValidationError("Trace exceeded safety limit; possible undocumented loop")

            for port_id in batch:
                scope.require(scope.get(Port, port_id), "cable:trace")
            terminations = self.session.scalars(
                self._query(CableTermination).where(CableTermination.port_id.in_(batch))
            ).all()
            cable_ids = {row.cable_id for row in terminations} - expanded_cables
            if cable_ids:
                cable_rows = self.session.scalars(
                    self._query(Cable).where(Cable.id.in_(cable_ids))
                ).all()
                for linked in cable_rows:
                    scope.require(linked, "cable:trace")
                if {row.id for row in cable_rows} != cable_ids:
                    raise NotFoundError("Connected cable not found in tenant")
                cables.update({c.id: c for c in cable_rows})
                all_terms = self.session.scalars(
                    self._query(CableTermination).where(CableTermination.cable_id.in_(cable_ids))
                ).all()
                grouped: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
                for term in all_terms:
                    grouped[term.cable_id].append(term.port_id)
                for cable_id, ports in grouped.items():
                    if len(ports) != 2:
                        continue
                    a, b = ports
                    adjacency[a].append(Edge(b, "cable", cable_id))
                    adjacency[b].append(Edge(a, "cable", cable_id))
                    for port in (a, b):
                        if port not in visited:
                            frontier.append(port)
                expanded_cables.update(cable_ids)

            mapping_rows = self.session.scalars(
                self._query(PortMapping).where(
                    or_(
                        PortMapping.source_port_id.in_(batch),
                        PortMapping.target_port_id.in_(batch),
                    )
                )
            ).all()
            for mapping in mapping_rows:
                if mapping.id in expanded_mappings:
                    continue
                a, b = mapping.source_port_id, mapping.target_port_id
                for port_id in (a, b):
                    scope.require(scope.get(Port, port_id), "cable:trace")
                mappings[mapping.id] = mapping
                adjacency[a].append(Edge(b, "mapping", mapping.id))
                adjacency[b].append(Edge(a, "mapping", mapping.id))
                for port in (a, b):
                    if port not in visited:
                        frontier.append(port)
                expanded_mappings.add(mapping.id)
        return adjacency, cables, mappings

    @staticmethod
    def _longest_path_containing_cable(
        adjacency: dict[uuid.UUID, list[Edge]], selected_cable_id: uuid.UUID
    ) -> tuple[list[uuid.UUID], list[Edge]]:
        leaves = [node for node, edges in adjacency.items() if len(edges) <= 1] or list(adjacency)[
            :1
        ]
        best_nodes: list[uuid.UUID] = []
        best_edges: list[Edge] = []

        def dfs(
            node: uuid.UUID,
            visited: set[uuid.UUID],
            nodes: list[uuid.UUID],
            edges: list[Edge],
        ) -> None:
            nonlocal best_nodes, best_edges
            contains = any(
                edge.kind == "cable" and edge.resource_id == selected_cable_id for edge in edges
            )
            if contains and len(nodes) > len(best_nodes):
                best_nodes, best_edges = list(nodes), list(edges)
            for edge in adjacency.get(node, []):
                if edge.neighbor in visited:
                    continue
                visited.add(edge.neighbor)
                nodes.append(edge.neighbor)
                edges.append(edge)
                dfs(edge.neighbor, visited, nodes, edges)
                edges.pop()
                nodes.pop()
                visited.remove(edge.neighbor)

        for leaf in leaves:
            dfs(leaf, {leaf}, [leaf], [])
        return best_nodes, best_edges

    def _port_descriptor(self, port: Port, scope: ResourceScope) -> dict[str, Any]:
        device = scope.get(Device, port.device_id)
        rack = scope.get(Rack, device.rack_id) if device.rack_id else None
        location = scope.get(Location, device.location_id)
        return {
            "kind": "port",
            "id": str(port.id),
            "identifier": port.identifier,
            "label": port.label,
            "connector_type": port.connector_type,
            "media_type": port.media_type,
            "face": port.front_or_rear,
            "device": {
                "id": str(device.id),
                "identifier": device.identifier,
                "name": device.name,
                "type": device.device_type,
            }
            if device
            else None,
            "rack": {"id": str(rack.id), "identifier": rack.rack_identifier} if rack else None,
            "location": {
                "id": str(location.id),
                "identifier": location.identifier,
                "name": location.name,
            }
            if location
            else None,
        }

    def _route_for_cable(
        self, cable_id: uuid.UUID, scope: ResourceScope | None = None
    ) -> list[dict[str, Any]]:
        scope = scope or ResourceScope(self.session, self.principal)
        scope.require(scope.get(Cable, cable_id), "cable:trace")
        rows = self.session.scalars(
            self._query(CableRouteSegment)
            .where(CableRouteSegment.cable_id == cable_id)
            .order_by(CableRouteSegment.sequence)
        ).all()
        result: list[dict[str, Any]] = []
        for route in rows:
            segment = scope.get(PathwaySegment, route.pathway_segment_id)
            pathway = scope.get(Pathway, segment.pathway_id)
            if segment and pathway:
                portion = {}
                if route.start_offset_m is not None:
                    geometry = saved_portion_geometry(segment, route)
                    portion = {
                        "full_length_m": segment.length_m,
                        "start_offset_m": route.start_offset_m,
                        "end_offset_m": route.end_offset_m,
                        "geometry_hash": route.geometry_hash,
                        "valid": geometry is not None,
                        "length_m": geometry.adopted_length(
                            route.start_offset_m, route.end_offset_m
                        )
                        if geometry
                        else None,
                        "coordinates": geometry.clip(route.start_offset_m, route.end_offset_m)
                        if geometry
                        else [],
                    }
                result.append(
                    {
                        "sequence": route.sequence,
                        "pathway_id": str(pathway.id),
                        "pathway": pathway.identifier,
                        "segment_id": str(segment.id),
                        "segment": segment.name,
                        "length_m": segment.length_m,
                        "coordinates": segment.coordinates,
                        **portion,
                    }
                )
        return result

    def trace_cable(self, cable_id: uuid.UUID) -> dict[str, Any]:
        scope = ResourceScope(self.session, self.principal)
        require_permission(scope.principal, "cable:trace")
        cable = scope.get(Cable, cable_id)
        scope.require(cable, "cable:trace")
        terms = self.session.scalars(
            self._query(CableTermination)
            .where(CableTermination.cable_id == cable.id)
            .order_by(CableTermination.side)
        ).all()
        if len(terms) != 2:
            raise ValidationError("Cable does not have exactly two terminations")
        adjacency, cables, mappings = self._expand_graph(
            [terms[0].port_id, terms[1].port_id], scope
        )
        nodes, edges = self._longest_path_containing_cable(adjacency, cable.id)
        if not nodes:
            nodes = [terms[0].port_id, terms[1].port_id]
            edges = [Edge(terms[1].port_id, "cable", cable.id)]
        port_rows = self.session.scalars(self._query(Port).where(Port.id.in_(nodes))).all()
        ports = {port.id: port for port in port_rows}
        items: list[dict[str, Any]] = []
        for index, node in enumerate(nodes):
            if node in ports:
                items.append(self._port_descriptor(ports[node], scope))
            if index >= len(edges):
                continue
            edge = edges[index]
            if edge.kind == "cable":
                linked = cables.get(edge.resource_id) or scope.get(Cable, edge.resource_id)
                if linked:
                    status = linked.installation_status
                    items.append(
                        {
                            "kind": "cable",
                            "id": str(linked.id),
                            "identifier": linked.identifier,
                            "media_type": linked.media_type,
                            "construction": linked.construction,
                            "status": status.value if hasattr(status, "value") else str(status),
                            "route": self._route_for_cable(linked.id, scope),
                            "selected": linked.id == cable.id,
                        }
                    )
            else:
                mapping = mappings.get(edge.resource_id)
                items.append(
                    {
                        "kind": "internal_mapping",
                        "id": str(edge.resource_id),
                        "mapping_type": mapping.mapping_type if mapping else "unknown",
                        "lane": mapping.lane if mapping else None,
                    }
                )
        return {
            "selected_cable": str(cable.id),
            "selected_identifier": cable.identifier,
            "complete": bool(nodes and edges),
            "hop_count": len(edges),
            "items": items,
        }

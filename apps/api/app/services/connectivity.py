from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import ConflictError, NotFoundError, ValidationError
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
    Rack,
)
from app.security import Principal, require_permission


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

    @staticmethod
    def _media_family(value: str) -> str:
        value = value.lower()
        if any(token in value for token in ("cat", "copper", "rj45", "punchdown")):
            return "copper"
        if any(token in value for token in ("fiber", "os", "om", "lc", "sc", "mpo", "mtp")):
            return "fiber"
        return value

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
    ) -> Cable:
        require_permission(self.principal, "cable:create")
        port_a = self.session.get(Port, port_a_id)
        port_b = self.session.get(Port, port_b_id)
        if not port_a or not port_b:
            raise NotFoundError("One or both termination ports were not found in tenant")
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
        self.session.add_all([
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
        ])
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
            },
            project_id=project_id,
        )
        return cable

    def _expand_graph(
        self, seed_ports: list[uuid.UUID]
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

            terminations = self.session.scalars(
                select(CableTermination).where(CableTermination.port_id.in_(batch))
            ).all()
            cable_ids = {row.cable_id for row in terminations} - expanded_cables
            if cable_ids:
                cable_rows = self.session.scalars(
                    select(Cable).where(Cable.id.in_(cable_ids))
                ).all()
                cables.update({c.id: c for c in cable_rows})
                all_terms = self.session.scalars(
                    select(CableTermination).where(CableTermination.cable_id.in_(cable_ids))
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
                select(PortMapping).where(
                    or_(
                        PortMapping.source_port_id.in_(batch),
                        PortMapping.target_port_id.in_(batch),
                    )
                )
            ).all()
            for mapping in mapping_rows:
                if mapping.id in expanded_mappings:
                    continue
                mappings[mapping.id] = mapping
                a, b = mapping.source_port_id, mapping.target_port_id
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
        leaves = [
            node for node, edges in adjacency.items() if len(edges) <= 1
        ] or list(adjacency)[:1]
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

    def _port_descriptor(self, port: Port) -> dict[str, Any]:
        device = self.session.get(Device, port.device_id)
        rack = self.session.get(Rack, device.rack_id) if device and device.rack_id else None
        location = self.session.get(Location, device.location_id) if device else None
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

    def _route_for_cable(self, cable_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = self.session.scalars(
            select(CableRouteSegment)
            .where(CableRouteSegment.cable_id == cable_id)
            .order_by(CableRouteSegment.sequence)
        ).all()
        result: list[dict[str, Any]] = []
        for route in rows:
            segment = self.session.get(PathwaySegment, route.pathway_segment_id)
            pathway = self.session.get(Pathway, segment.pathway_id) if segment else None
            if segment and pathway:
                result.append(
                    {
                        "sequence": route.sequence,
                        "pathway_id": str(pathway.id),
                        "pathway": pathway.identifier,
                        "segment_id": str(segment.id),
                        "segment": segment.name,
                        "length_m": segment.length_m,
                        "coordinates": segment.coordinates,
                    }
                )
        return result

    def trace_cable(self, cable_id: uuid.UUID) -> dict[str, Any]:
        require_permission(self.principal, "cable:trace")
        cable = self.session.get(Cable, cable_id)
        if not cable:
            raise NotFoundError("Cable not found in tenant")
        terms = self.session.scalars(
            select(CableTermination)
            .where(CableTermination.cable_id == cable.id)
            .order_by(CableTermination.side)
        ).all()
        if len(terms) != 2:
            raise ValidationError("Cable does not have exactly two terminations")
        adjacency, cables, mappings = self._expand_graph([terms[0].port_id, terms[1].port_id])
        nodes, edges = self._longest_path_containing_cable(adjacency, cable.id)
        if not nodes:
            nodes = [terms[0].port_id, terms[1].port_id]
            edges = [Edge(terms[1].port_id, "cable", cable.id)]
        port_rows = self.session.scalars(select(Port).where(Port.id.in_(nodes))).all()
        ports = {port.id: port for port in port_rows}
        items: list[dict[str, Any]] = []
        for index, node in enumerate(nodes):
            if node in ports:
                items.append(self._port_descriptor(ports[node]))
            if index >= len(edges):
                continue
            edge = edges[index]
            if edge.kind == "cable":
                linked = cables.get(edge.resource_id) or self.session.get(Cable, edge.resource_id)
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
                            "route": self._route_for_cable(linked.id),
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

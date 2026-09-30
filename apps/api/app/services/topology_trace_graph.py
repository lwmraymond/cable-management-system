"""Topology trace mixin: TopologyTraceGraphMixin."""
from __future__ import annotations

import uuid
from collections import defaultdict, deque
from typing import Any, Iterable

from sqlalchemy import or_, select

from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.fiber_models import (
    ChannelMember,
    ConnectivityChannel,
    CopperPair,
    FiberBreakout,
    FiberBreakoutLeg,
    FiberBundle,
    FiberCassette,
    FiberCassetteSlot,
    FiberEndpointClaim,
    FiberPortTermination,
    FiberSplice,
    FiberSpliceEnd,
    FiberStrand,
    OtdrEvent,
    OtdrRecord,
    PhysicalPortClaim,
)
from app.models import (
    Cable, CableTermination, Device, Location, Port, PortMapping, Project, Rack, Tenant,
)
from app.security import require_permission, resolve_principal
from app.services.connectivity import ConnectivityService
from app.services.topology_trace_types import Node, TopologyEdge, fiber_node, port_node

class TopologyTraceGraphMixin:
    def __init__(self, session, principal):
        super().__init__(session, principal)
        self._edge_keys: set[tuple[str, str, str, str]] = set()
        self._adjacency: dict[Node, list[TopologyEdge]] = defaultdict(list)
        self._truncated = False

    @staticmethod
    def _node_key(node: Node) -> str:
        return f"{node[0]}:{node[1]}:{node[2]}"

    def _authorize_trace(self, project_id: uuid.UUID) -> None:
        tenant = self.db.scalar(select(Tenant).where(
            Tenant.id == self.tenant_id,
            Tenant.active.is_(True),
        ))
        if tenant is None:
            raise AuthorizationError("Tenant is inactive or unavailable")
        self._get(Project, project_id)
        actual = resolve_principal(
            self.db,
            actor_id=self.principal.actor_id,
            tenant_id=self.tenant_id,
            project_id=project_id,
            request_id=self.principal.request_id,
            ip_address=self.principal.ip_address,
            user_agent=self.principal.user_agent,
        )
        require_permission(actual, "cable:trace")

    def _add_edge(self, left: Node, right: Node, kind: str, resource_id: uuid.UUID) -> None:
        if left == right:
            raise ConflictError("Topology contains a self-loop")
        a, b = sorted((self._node_key(left), self._node_key(right)))
        key = (a, b, kind, str(resource_id))
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self._adjacency[left].append(TopologyEdge(right, kind, resource_id))
        self._adjacency[right].append(TopologyEdge(left, kind, resource_id))

    def _active_endpoint_claim(self, strand_id: uuid.UUID, side: str):
        claim = self.db.scalar(select(FiberEndpointClaim).where(
            FiberEndpointClaim.tenant_id == self.tenant_id,
            FiberEndpointClaim.strand_id == strand_id,
            FiberEndpointClaim.side == side,
            FiberEndpointClaim.deleted_at.is_(None),
        ))
        if claim is not None:
            return claim.owner_type, claim.owner_id
        legacy = self.db.scalar(select(FiberSpliceEnd).where(
            FiberSpliceEnd.tenant_id == self.tenant_id,
            FiberSpliceEnd.strand_id == strand_id,
            FiberSpliceEnd.side == side,
            FiberSpliceEnd.deleted_at.is_(None),
        ))
        return ("splice", legacy.splice_id) if legacy else None

    def _active_port_claim(self, port_id: uuid.UUID):
        claim = self.db.scalar(select(PhysicalPortClaim).where(
            PhysicalPortClaim.tenant_id == self.tenant_id,
            PhysicalPortClaim.port_id == port_id,
            PhysicalPortClaim.deleted_at.is_(None),
        ))
        if claim is not None:
            return claim.owner_type, claim.owner_id
        legacy = self.db.scalar(select(CableTermination).where(
            CableTermination.tenant_id == self.tenant_id,
            CableTermination.port_id == port_id,
            CableTermination.deleted_at.is_(None),
        ))
        return ("cable_termination", legacy.id) if legacy else None

    def _expand_fiber_node(self, node: Node) -> Iterable[Node]:
        _, strand_id, side = node
        strand = self._get(FiberStrand, strand_id)
        other_side = "B" if side == "A" else "A"
        opposite = fiber_node(strand.id, other_side)
        self._add_edge(node, opposite, "fiber_strand", strand.id)
        yield opposite

        claim = self._active_endpoint_claim(strand.id, side)
        if claim is None:
            return
        owner_type, owner_id = claim
        if owner_type == "splice":
            splice = self._get(FiberSplice, owner_id)
            ends = self.db.scalars(select(FiberSpliceEnd).where(
                FiberSpliceEnd.tenant_id == self.tenant_id,
                FiberSpliceEnd.splice_id == splice.id,
                FiberSpliceEnd.deleted_at.is_(None),
            ).order_by(FiberSpliceEnd.end_number).limit(3)).all()
            if len(ends) != 2:
                raise ConflictError("Incomplete splice topology; repair is required")
            local = next(
                (end for end in ends if end.strand_id == strand.id and end.side == side),
                None,
            )
            remote = next((end for end in ends if local is not None and end.id != local.id), None)
            if local is None or remote is None:
                raise ConflictError("Splice claim does not match its endpoint rows")
            neighbor = fiber_node(remote.strand_id, remote.side)
            self._add_edge(node, neighbor, "splice", splice.id)
            yield neighbor
        elif owner_type == "breakout_leg":
            leg = self._get(FiberBreakoutLeg, owner_id)
            if leg.parent_strand_id == strand.id and leg.parent_side == side:
                neighbor = fiber_node(leg.child_strand_id, leg.child_side)
            elif leg.child_strand_id == strand.id and leg.child_side == side:
                neighbor = fiber_node(leg.parent_strand_id, leg.parent_side)
            else:
                raise ConflictError("Breakout claim does not match its leg")
            self._add_edge(node, neighbor, "breakout", leg.id)
            yield neighbor
        elif owner_type == "fiber_termination":
            termination = self._get(FiberPortTermination, owner_id)
            if termination.strand_id != strand.id or termination.side != side:
                raise ConflictError("Fiber termination claim does not match its endpoint")
            neighbor = port_node(termination.port_id)
            self._add_edge(node, neighbor, "fiber_termination", termination.id)
            yield neighbor
        else:
            raise ConflictError("Unknown fiber endpoint claim type")

    def _expand_port_node(self, node: Node) -> Iterable[Node]:
        _, port_id, _ = node
        self._get(Port, port_id)
        mappings = self.db.scalars(select(PortMapping).where(
            PortMapping.tenant_id == self.tenant_id,
            PortMapping.deleted_at.is_(None),
            or_(PortMapping.source_port_id == port_id, PortMapping.target_port_id == port_id),
        ).order_by(PortMapping.id).limit(100)).all()
        for mapping in mappings:
            remote_id = (
                mapping.target_port_id
                if mapping.source_port_id == port_id
                else mapping.source_port_id
            )
            neighbor = port_node(remote_id)
            self._add_edge(node, neighbor, "internal_mapping", mapping.id)
            yield neighbor

        claim = self._active_port_claim(port_id)
        if claim is None:
            return
        owner_type, owner_id = claim
        if owner_type == "fiber_termination":
            termination = self._get(FiberPortTermination, owner_id)
            if termination.port_id != port_id:
                raise ConflictError("Physical port claim does not match fiber termination")
            neighbor = fiber_node(termination.strand_id, termination.side)
            self._add_edge(node, neighbor, "fiber_termination", termination.id)
            yield neighbor
        elif owner_type == "cable_termination":
            termination = self._get(CableTermination, owner_id)
            if termination.port_id != port_id:
                raise ConflictError("Physical port claim does not match cable termination")
            terms = self.db.scalars(select(CableTermination).where(
                CableTermination.tenant_id == self.tenant_id,
                CableTermination.cable_id == termination.cable_id,
                CableTermination.deleted_at.is_(None),
            ).order_by(CableTermination.side).limit(3)).all()
            if len(terms) != 2:
                raise ConflictError("Cable does not have exactly two active terminations")
            remote = next((item for item in terms if item.id != termination.id), None)
            if remote is None:
                raise ConflictError("Cable termination topology is invalid")
            neighbor = port_node(remote.port_id)
            self._add_edge(node, neighbor, "cable", termination.cable_id)
            yield neighbor
        else:
            raise ConflictError("Unknown physical port claim type")

    def _build_graph(self, seeds: list[Node], max_nodes: int) -> dict[Node, list[TopologyEdge]]:
        if type(max_nodes) is not int or not 2 <= max_nodes <= self.MAX_TRACE_NODES:
            raise ValidationError("Trace max_nodes must be between 2 and 1000")
        self._edge_keys.clear()
        self._adjacency.clear()
        self._truncated = False
        visited: set[Node] = set()
        queued: set[Node] = set(seeds)
        queue: deque[Node] = deque(seeds)
        while queue:
            node = queue.popleft()
            queued.discard(node)
            if node in visited:
                continue
            if len(visited) >= max_nodes:
                self._truncated = True
                break
            visited.add(node)
            if node[0] == "fiber":
                neighbors = self._expand_fiber_node(node)
            elif node[0] == "port":
                neighbors = self._expand_port_node(node)
            else:
                raise ConflictError("Unknown topology node type")
            for neighbor in neighbors:
                if neighbor not in visited and neighbor not in queued:
                    queue.append(neighbor)
                    queued.add(neighbor)
        # Expansion methods add undirected edges before the queue admits the
        # neighbor. When the safety cap is hit, remove any not-yet-visited fringe
        # so the returned graph never exceeds max_nodes even by one layer.
        for node in list(self._adjacency):
            if node not in visited:
                del self._adjacency[node]
                continue
            self._adjacency[node] = [
                edge for edge in self._adjacency[node] if edge.neighbor in visited
            ]
            self._adjacency[node].sort(
                key=lambda edge: (edge.kind, str(edge.resource_id), self._node_key(edge.neighbor))
            )
        for node in visited:
            self._adjacency.setdefault(node, [])
        retained_keys: set[tuple[str, str, str, str]] = set()
        for node, rows in self._adjacency.items():
            for edge in rows:
                a, b = sorted((self._node_key(node), self._node_key(edge.neighbor)))
                retained_keys.add((a, b, edge.kind, str(edge.resource_id)))
        self._edge_keys = retained_keys
        return self._adjacency

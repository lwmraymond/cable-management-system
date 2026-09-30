"""Topology trace mixin: TopologyTracePathMixin."""
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

class TopologyTracePathMixin:
    @staticmethod
    def _same_edge(
        left: Node,
        edge: TopologyEdge,
        selected_left: Node,
        selected_right: Node,
        selected_kind: str,
        selected_id: uuid.UUID,
    ) -> bool:
        return (
            edge.kind == selected_kind
            and edge.resource_id == selected_id
            and {left, edge.neighbor} == {selected_left, selected_right}
        )

    def _farthest_path(
        self,
        start: Node,
        *,
        selected_left: Node,
        selected_right: Node,
        selected_kind: str,
        selected_id: uuid.UUID,
        forbidden: set[Node] | None = None,
    ) -> list[Node]:
        forbidden = forbidden or set()
        queue: deque[Node] = deque([start])
        parent: dict[Node, Node | None] = {start: None}
        distance: dict[Node, int] = {start: 0}
        while queue:
            current = queue.popleft()
            for edge in self._adjacency.get(current, []):
                if self._same_edge(
                    current,
                    edge,
                    selected_left,
                    selected_right,
                    selected_kind,
                    selected_id,
                ):
                    continue
                neighbor = edge.neighbor
                if neighbor in forbidden or neighbor in parent:
                    continue
                parent[neighbor] = current
                distance[neighbor] = distance[current] + 1
                queue.append(neighbor)
        farthest = max(
            parent,
            key=lambda node: (distance[node], self._node_key(node)),
        )
        path = []
        current: Node | None = farthest
        while current is not None:
            path.append(current)
            current = parent[current]
        path.reverse()  # start -> farthest
        return path

    def _path_containing_selected(
        self,
        selected_left: Node,
        selected_right: Node,
        selected_kind: str,
        selected_id: uuid.UUID,
    ) -> tuple[list[Node], list[TopologyEdge]]:
        left_path = self._farthest_path(
            selected_left,
            selected_left=selected_left,
            selected_right=selected_right,
            selected_kind=selected_kind,
            selected_id=selected_id,
            forbidden={selected_right},
        )
        # Prevent an alternate-cycle path from reusing the left extension.
        right_path = self._farthest_path(
            selected_right,
            selected_left=selected_left,
            selected_right=selected_right,
            selected_kind=selected_kind,
            selected_id=selected_id,
            forbidden=set(left_path),
        )
        nodes = list(reversed(left_path)) + right_path
        edges: list[TopologyEdge] = []
        for left, right in zip(nodes, nodes[1:]):
            match = next(
                (edge for edge in self._adjacency.get(left, []) if edge.neighbor == right),
                None,
            )
            if match is None:
                raise ConflictError("Trace path contains a missing edge")
            edges.append(match)
        return nodes, edges

    def _channels_for(self, *, strand_id: uuid.UUID | None = None,
                      pair_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        statement = select(ChannelMember, ConnectivityChannel).join(
            ConnectivityChannel,
            (ConnectivityChannel.id == ChannelMember.channel_id)
            & (ConnectivityChannel.tenant_id == ChannelMember.tenant_id),
        ).where(
            ChannelMember.tenant_id == self.tenant_id,
            ChannelMember.deleted_at.is_(None),
            ConnectivityChannel.deleted_at.is_(None),
        )
        if strand_id is not None:
            statement = statement.where(ChannelMember.fiber_strand_id == strand_id)
        elif pair_id is not None:
            statement = statement.where(ChannelMember.copper_pair_id == pair_id)
        else:
            return []
        rows = self.db.execute(statement.order_by(ConnectivityChannel.identifier)).all()
        return [{
            "id": str(channel.id),
            "identifier": channel.identifier,
            "name": channel.name,
            "topology": channel.topology,
            "status": channel.status,
            "role": member.role,
            "sequence": member.sequence,
        } for member, channel in rows]

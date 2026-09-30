"""Topology trace mixin: TopologyTraceEntryMixin."""
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

class TopologyTraceEntryMixin:
    def trace_cable(
        self,
        cable_id: uuid.UUID,
        *,
        strand_number: int | None = None,
        pair_number: int | None = None,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        cable = self._get(Cable, cable_id)
        if cable.project_id is None:
            raise ValidationError("Cable must belong to a project before tracing")
        self._authorize_trace(cable.project_id)
        if strand_number is not None and pair_number is not None:
            raise ValidationError("Select either strand_number or pair_number, not both")
        bundle = self.db.scalar(select(FiberBundle).where(
            FiberBundle.tenant_id == self.tenant_id,
            FiberBundle.cable_id == cable.id,
            FiberBundle.deleted_at.is_(None),
        ))
        if bundle is None:
            legacy = ConnectivityService(self.db, self.principal).trace_cable(cable.id)
            selected_pair = None
            if pair_number is not None:
                if type(pair_number) is not int or not 1 <= pair_number <= 600:
                    raise ValidationError("pair_number must be between 1 and 600")
                pair = self.db.scalar(select(CopperPair).where(
                    CopperPair.tenant_id == self.tenant_id,
                    CopperPair.cable_id == cable.id,
                    CopperPair.number == pair_number,
                    CopperPair.deleted_at.is_(None),
                ))
                if pair is None:
                    raise NotFoundError("Selected cable pair is not provisioned")
                selected_pair = {
                    "id": str(pair.id),
                    "number": pair.number,
                    "color_code": pair.color_code,
                    "channels": self._channels_for(pair_id=pair.id),
                }
            return {
                **legacy,
                "trace_model": "generic-copper",
                "selected_pair": selected_pair,
                "selected_strand": None,
                "cycle": False,
                "branching": False,
                "truncated": False,
                "otdr": [],
            }

        if pair_number is not None:
            raise ValidationError("pair_number cannot be used with a provisioned fiber cable")
        selected_number = 1 if strand_number is None else strand_number
        if type(selected_number) is not int or not 1 <= selected_number <= bundle.strand_count:
            raise ValidationError(
                f"strand_number must be between 1 and {bundle.strand_count}"
            )
        strand = self.db.scalar(select(FiberStrand).where(
            FiberStrand.tenant_id == self.tenant_id,
            FiberStrand.bundle_id == bundle.id,
            FiberStrand.number == selected_number,
            FiberStrand.deleted_at.is_(None),
        ))
        if strand is None:
            raise NotFoundError("Selected fiber strand is not provisioned")
        left = fiber_node(strand.id, "A")
        right = fiber_node(strand.id, "B")
        adjacency = self._build_graph([left, right], max_nodes)
        nodes, edges = self._path_containing_selected(
            left,
            right,
            "fiber_strand",
            strand.id,
        )
        items: list[dict[str, Any]] = []
        for index, node in enumerate(nodes):
            items.append(
                self._fiber_node_descriptor(node)
                if node[0] == "fiber"
                else self._port_descriptor(node)
            )
            if index < len(edges):
                items.append(self._edge_descriptor(
                    edges[index],
                    selected_kind="fiber_strand",
                    selected_id=strand.id,
                ))
        unique_edge_count = len(self._edge_keys)
        unique_node_count = len(adjacency)
        cycle = unique_edge_count >= unique_node_count and unique_node_count > 0
        branching = any(len(edge_rows) > 2 for edge_rows in adjacency.values())
        return {
            "selected_cable": str(cable.id),
            "selected_identifier": cable.identifier,
            "trace_model": "generic-fiber",
            "selected_strand": {
                "id": str(strand.id),
                "number": strand.number,
                "bundle_id": str(bundle.id),
                "channels": self._channels_for(strand_id=strand.id),
            },
            "selected_pair": None,
            "complete": bool(nodes and edges) and not self._truncated,
            "hop_count": len(edges),
            "node_count": unique_node_count,
            "edge_count": unique_edge_count,
            "cycle": cycle,
            "branching": branching,
            "truncated": self._truncated,
            "items": items,
            "otdr": self._otdr_for(cable.id, strand.id),
        }

    def trace_channel(self, channel_id: uuid.UUID, *, max_nodes: int = 500) -> dict[str, Any]:
        channel = self._get(ConnectivityChannel, channel_id)
        self._authorize_trace(channel.project_id)
        members = self.db.scalars(select(ChannelMember).where(
            ChannelMember.tenant_id == self.tenant_id,
            ChannelMember.channel_id == channel.id,
            ChannelMember.deleted_at.is_(None),
        ).order_by(ChannelMember.sequence).limit(self.MAX_MEMBERS)).all()
        traces = []
        for member in members:
            if member.fiber_strand_id:
                strand = self._get(FiberStrand, member.fiber_strand_id)
                bundle = self._get(FiberBundle, strand.bundle_id)
                traces.append({
                    "sequence": member.sequence,
                    "role": member.role,
                    "trace": self.trace_cable(
                        bundle.cable_id,
                        strand_number=strand.number,
                        max_nodes=max_nodes,
                    ),
                })
            elif member.copper_pair_id:
                pair = self._get(CopperPair, member.copper_pair_id)
                traces.append({
                    "sequence": member.sequence,
                    "role": member.role,
                    "trace": self.trace_cable(
                        pair.cable_id,
                        pair_number=pair.number,
                        max_nodes=max_nodes,
                    ),
                })
            else:
                raise ConflictError("Channel member has no physical resource")
        return {
            "id": str(channel.id),
            "identifier": channel.identifier,
            "name": channel.name,
            "medium": channel.medium,
            "topology": channel.topology,
            "status": channel.status,
            "members": traces,
        }

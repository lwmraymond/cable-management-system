"""Topology trace mixin: TopologyTraceDescribeMixin."""
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

class TopologyTraceDescribeMixin:
    def _fiber_node_descriptor(self, node: Node) -> dict[str, Any]:
        _, strand_id, side = node
        strand = self._get(FiberStrand, strand_id)
        bundle = self._get(FiberBundle, strand.bundle_id)
        cable = self._get(Cable, bundle.cable_id)
        return {
            "kind": "fiber_endpoint",
            "strand_id": str(strand.id),
            "side": side,
            "number": strand.number,
            "bundle_id": str(bundle.id),
            "cable": {
                "id": str(cable.id),
                "identifier": cable.identifier,
                "media_type": cable.media_type,
            },
            "channels": self._channels_for(strand_id=strand.id),
        }

    def _port_descriptor(self, node: Node) -> dict[str, Any]:
        _, port_id, _ = node
        port = self._get(Port, port_id)
        device = self._get(Device, port.device_id)
        rack = self.db.scalar(select(Rack).where(
            Rack.id == device.rack_id,
            Rack.tenant_id == self.tenant_id,
            Rack.deleted_at.is_(None),
        )) if device.rack_id else None
        location = self._get(Location, device.location_id)
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
            },
            "rack": {
                "id": str(rack.id),
                "identifier": rack.rack_identifier,
            } if rack else None,
            "location": {
                "id": str(location.id),
                "identifier": location.identifier,
                "name": location.name,
            },
        }

    def _edge_descriptor(
        self,
        edge: TopologyEdge,
        *,
        selected_kind: str,
        selected_id: uuid.UUID,
    ) -> dict[str, Any]:
        selected = edge.kind == selected_kind and edge.resource_id == selected_id
        if edge.kind == "fiber_strand":
            strand = self._get(FiberStrand, edge.resource_id)
            bundle = self._get(FiberBundle, strand.bundle_id)
            cable = self._get(Cable, bundle.cable_id)
            return {
                "kind": "fiber_strand",
                "id": str(strand.id),
                "number": strand.number,
                "bundle_id": str(bundle.id),
                "cable_id": str(cable.id),
                "identifier": cable.identifier,
                "media_type": cable.media_type,
                "length_m": cable.measured_length_m or cable.length_m,
                "selected": selected,
                "channels": self._channels_for(strand_id=strand.id),
            }
        if edge.kind == "cable":
            cable = self._get(Cable, edge.resource_id)
            status = cable.installation_status
            return {
                "kind": "cable",
                "id": str(cable.id),
                "identifier": cable.identifier,
                "media_type": cable.media_type,
                "construction": cable.construction,
                "status": status.value if hasattr(status, "value") else str(status),
                "route": ConnectivityService(self.db, self.principal)._route_for_cable(cable.id),
                "selected": selected,
            }
        if edge.kind == "internal_mapping":
            mapping = self._get(PortMapping, edge.resource_id)
            return {
                "kind": "internal_mapping",
                "id": str(mapping.id),
                "mapping_type": mapping.mapping_type,
                "lane": mapping.lane,
                "selected": selected,
            }
        if edge.kind == "splice":
            splice = self._get(FiberSplice, edge.resource_id)
            slot = self._get(FiberCassetteSlot, splice.slot_id)
            cassette = self._get(FiberCassette, slot.cassette_id)
            return {
                "kind": "splice",
                "id": str(splice.id),
                "cassette_id": str(cassette.id),
                "cassette": cassette.name,
                "slot_id": str(slot.id),
                "slot_number": slot.number,
                "loss_db": splice.loss_db,
                "selected": selected,
            }
        if edge.kind == "breakout":
            leg = self._get(FiberBreakoutLeg, edge.resource_id)
            breakout = self._get(FiberBreakout, leg.breakout_id)
            return {
                "kind": "breakout",
                "id": str(leg.id),
                "breakout_id": str(breakout.id),
                "identifier": breakout.identifier,
                "mode": breakout.mode,
                "leg_number": leg.leg_number,
                "label": leg.label,
                "loss_db": leg.loss_db,
                "selected": selected,
            }
        if edge.kind == "fiber_termination":
            termination = self._get(FiberPortTermination, edge.resource_id)
            return {
                "kind": "fiber_termination",
                "id": str(termination.id),
                "connection_type": termination.connection_type,
                "loss_db": termination.loss_db,
                "selected": selected,
            }
        raise ConflictError("Unknown topology edge type")

    def _otdr_for(self, cable_id: uuid.UUID, strand_id: uuid.UUID | None) -> list[dict[str, Any]]:
        statement = select(OtdrRecord).where(
            OtdrRecord.tenant_id == self.tenant_id,
            OtdrRecord.cable_id == cable_id,
            OtdrRecord.deleted_at.is_(None),
        )
        if strand_id is not None:
            statement = statement.where(
                or_(OtdrRecord.strand_id == strand_id, OtdrRecord.strand_id.is_(None))
            )
        records = self.db.scalars(statement.order_by(
            OtdrRecord.acquired_at.desc(), OtdrRecord.id,
        ).limit(self.MAX_OTDR_RECORDS)).all()
        result = []
        for record in records:
            events = self.db.scalars(select(OtdrEvent).where(
                OtdrEvent.tenant_id == self.tenant_id,
                OtdrEvent.record_id == record.id,
                OtdrEvent.deleted_at.is_(None),
            ).order_by(OtdrEvent.sequence).limit(10_000)).all()
            result.append({
                "id": str(record.id),
                "direction": record.direction,
                "wavelength_nm": record.wavelength_nm,
                "acquired_at": record.acquired_at.isoformat(),
                "total_length_m": record.total_length_m,
                "end_to_end_loss_db": record.end_to_end_loss_db,
                "events": [{
                    "id": str(event.id),
                    "sequence": event.sequence,
                    "distance_m": event.distance_m,
                    "event_type": event.event_type,
                    "loss_db": event.loss_db,
                    "reflectance_db": event.reflectance_db,
                    "confidence": event.confidence,
                    "linked_kind": event.linked_kind,
                    "linked_id": str(event.linked_id) if event.linked_id else None,
                    "link_offset_m": event.link_offset_m,
                } for event in events],
            })
        return result

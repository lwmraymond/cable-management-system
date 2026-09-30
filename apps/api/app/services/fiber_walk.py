"""FiberService mixin: FiberWalkMixin."""
from __future__ import annotations

import math
import uuid
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.fiber_models import (
    FiberBreakoutLeg, FiberBundle, FiberCassette, FiberCassetteSlot,
    FiberEndpointClaim, FiberPortTermination, FiberSplice, FiberSpliceEnd, FiberStrand,
)
from app.models import Cable, Device, Location, Project, Tenant, utcnow
from app.security import Principal, require_permission, resolve_principal

class FiberWalkMixin:
    def trace(self, strand_id, entry_side="A", max_hops=64):
        _, cable = self._strand(strand_id)
        # A whole-cable trace is broader than a single location-scoped splice operation.
        self._authorize("read", cable.project_id)
        return self._walk(strand_id, entry_side, max_hops, project_id=cable.project_id)

    def _walk(self, strand_id, entry_side, max_hops, *, project_id, cassette_scope=None):
        if entry_side not in ("A", "B") or type(max_hops) is not int or \
                not 1 <= max_hops <= self.MAX_TRACE_HOPS:
            raise ValidationError("Invalid side or trace hop limit")
        visited = set()
        steps = []
        total_loss = 0.0
        termination = "limit"
        for _ in range(max_hops):
            if strand_id in visited:
                termination = "cycle"
                break
            visited.add(strand_id)
            strand, cable = self._strand(strand_id)
            if cable.project_id != project_id:
                raise AuthorizationError("Trace crosses a project boundary")
            if cassette_scope is None:
                self._authorize("read", project_id)
            exit_side = "B" if entry_side == "A" else "A"
            steps.append({"kind": "strand", "strand_id": str(strand.id),
                          "cable_id": str(cable.id), "number": strand.number,
                          "entry_side": entry_side, "exit_side": exit_side})

            endpoint = self.db.scalar(select(FiberEndpointClaim).where(
                FiberEndpointClaim.tenant_id == self.tenant_id,
                FiberEndpointClaim.strand_id == strand.id,
                FiberEndpointClaim.side == exit_side,
                FiberEndpointClaim.deleted_at.is_(None),
            ))

            # Backward-compatible repair visibility for pre-005 rows or manually
            # corrupted test data that has a splice end but no normalized claim.
            legacy_end = None
            if endpoint is None:
                legacy_end = self.db.scalar(select(FiberSpliceEnd).where(
                    FiberSpliceEnd.tenant_id == self.tenant_id,
                    FiberSpliceEnd.strand_id == strand.id,
                    FiberSpliceEnd.side == exit_side,
                    FiberSpliceEnd.deleted_at.is_(None),
                ))
                if legacy_end is None:
                    termination = "open"
                    break
                owner_type, owner_id = "splice", legacy_end.splice_id
            else:
                owner_type, owner_id = endpoint.owner_type, endpoint.owner_id

            if owner_type == "fiber_termination":
                link = self._get(FiberPortTermination, owner_id)
                if (
                    link.project_id != project_id
                    or link.strand_id != strand.id
                    or link.side != exit_side
                ):
                    raise ConflictError("Invalid fiber termination claim")
                steps.append({"kind": "termination", "termination_id": str(link.id),
                              "port_id": str(link.port_id),
                              "connection_type": link.connection_type,
                              "loss_db": link.loss_db})
                total_loss += link.loss_db
                termination = "port"
                break

            if owner_type == "breakout_leg":
                leg = self._get(FiberBreakoutLeg, owner_id)
                if leg.parent_strand_id == strand.id and leg.parent_side == exit_side:
                    next_id, next_side = leg.child_strand_id, leg.child_side
                elif leg.child_strand_id == strand.id and leg.child_side == exit_side:
                    next_id, next_side = leg.parent_strand_id, leg.parent_side
                else:
                    raise ConflictError("Invalid breakout endpoint claim")
                _, next_cable = self._strand(next_id)
                if next_cable.project_id != project_id:
                    raise AuthorizationError("Trace crosses a project boundary")
                steps.append({"kind": "breakout", "breakout_leg_id": str(leg.id),
                              "breakout_id": str(leg.breakout_id),
                              "leg_number": leg.leg_number, "loss_db": leg.loss_db})
                total_loss += leg.loss_db
                strand_id, entry_side = next_id, next_side
            elif owner_type == "splice":
                claim = legacy_end or self.db.scalar(select(FiberSpliceEnd).where(
                    FiberSpliceEnd.tenant_id == self.tenant_id,
                    FiberSpliceEnd.splice_id == owner_id,
                    FiberSpliceEnd.strand_id == strand.id,
                    FiberSpliceEnd.side == exit_side,
                    FiberSpliceEnd.deleted_at.is_(None),
                ))
                if claim is None:
                    raise ConflictError("Splice claim has no matching endpoint")
                splice = self._get(FiberSplice, owner_id)
                slot = self._get(FiberCassetteSlot, splice.slot_id)
                remote_cassette = self._cassette(slot.cassette_id,
                                                 "write" if cassette_scope else "read")
                if remote_cassette.project_id != project_id:
                    raise AuthorizationError("Trace crosses a project boundary")
                ends = self.db.scalars(select(FiberSpliceEnd).where(
                    FiberSpliceEnd.tenant_id == self.tenant_id,
                    FiberSpliceEnd.splice_id == splice.id,
                    FiberSpliceEnd.deleted_at.is_(None),
                ).order_by(FiberSpliceEnd.end_number).limit(3)).all()
                if len(ends) != 2 or {e.end_number for e in ends} != {1, 2}:
                    raise ConflictError("Incomplete splice topology; repair is required")
                other = next((e for e in ends if e.id != claim.id), None)
                if other is None:
                    raise ConflictError("Invalid splice endpoint topology")
                _, next_cable = self._strand(other.strand_id)
                if next_cable.project_id != project_id:
                    raise AuthorizationError("Trace crosses a project boundary")
                steps.append({"kind": "splice", "splice_id": str(splice.id),
                              "cassette_id": str(remote_cassette.id),
                              "slot_number": slot.number, "loss_db": splice.loss_db})
                total_loss += splice.loss_db
                strand_id, entry_side = other.strand_id, other.side
            else:
                raise ConflictError("Unknown fiber endpoint claim type")

            if strand_id in visited:
                termination = "cycle"
                break
        rounded_loss = round(total_loss, 6)
        return {"steps": steps, "termination": termination,
                "truncated": termination == "limit", "cycle": termination == "cycle",
                "total_splice_loss_db": rounded_loss,
                "total_topology_loss_db": rounded_loss, "max_hops": max_hops}

    def find_bundle(self, cable_id):
        cable = self._cable(cable_id)
        self._authorize("read", cable.project_id)
        bundle_id = self.db.scalar(select(FiberBundle.id).where(
            FiberBundle.tenant_id == self.tenant_id, FiberBundle.cable_id == cable.id,
            FiberBundle.deleted_at.is_(None),
        ))
        if bundle_id is None:
            raise NotFoundError("Cable has no provisioned fiber bundle")
        return self.get_bundle(bundle_id)

    def list_cassettes(self, device_id, project_id, limit=100):
        device = self._get(Device, device_id)
        self._authorize("read", project_id, device.location_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError("Invalid cassette list limit")
        rows = self.db.scalars(select(FiberCassette).where(
            FiberCassette.tenant_id == self.tenant_id, FiberCassette.device_id == device.id,
            FiberCassette.project_id == project_id, FiberCassette.deleted_at.is_(None),
        ).order_by(FiberCassette.name, FiberCassette.id).limit(limit + 1)).all()
        return {"items": [{"id": str(c.id), "name": c.name, "slot_count": c.slot_count}
                          for c in rows[:limit]], "truncated": len(rows) > limit}

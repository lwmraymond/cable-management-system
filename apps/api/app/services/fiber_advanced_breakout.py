"""Advanced Fiber mixin: FiberAdvancedBreakoutMixin."""
from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update

from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.fiber_models import (
    ChannelMember,
    ConnectivityChannel,
    CopperPair,
    FiberBreakout,
    FiberBreakoutLeg,
    FiberEndpointClaim,
    FiberPortTermination,
    FiberSplice,
    FiberSpliceEnd,
    OtdrEvent,
    OtdrRecord,
    PhysicalPortClaim,
)
from app.models import Cable, CableTermination, Device, Port, utcnow

class FiberAdvancedBreakoutMixin:
    def create_breakout(
        self,
        *,
        project_id: uuid.UUID,
        device_id: uuid.UUID,
        identifier: str,
        name: str,
        mode: str,
        legs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        identifier = self._identifier(identifier)
        name = self._name(name)
        if mode not in {"fanout", "fanin", "passive"}:
            raise ValidationError("Unsupported breakout mode")
        if not isinstance(legs, list) or not 1 <= len(legs) <= 576:
            raise ValidationError("Breakout requires 1 to 576 legs")
        device = self._get(Device, device_id)
        self._authorize("write", project_id, device.location_id)
        self._project_lock(project_id)
        breakout = FiberBreakout(
            tenant_id=self.tenant_id,
            project_id=project_id,
            device_id=device.id,
            identifier=identifier,
            name=name,
            mode=mode,
        )
        self.db.add(breakout)
        self.db.flush()
        endpoint_set: set[tuple[uuid.UUID, str]] = set()
        rows: list[FiberBreakoutLeg] = []
        # Validate and write each leg before checking the next one. A later cycle
        # check therefore sees earlier legs in the same request; any failure rolls
        # back the complete breakout transaction.
        for number, raw in enumerate(legs, start=1):
            if not isinstance(raw, dict):
                raise ValidationError("Each breakout leg must be an object")
            try:
                parent_id = uuid.UUID(str(raw.get("parent_strand_id")))
                child_id = uuid.UUID(str(raw.get("child_strand_id")))
            except (TypeError, ValueError):
                raise ValidationError("Breakout strand IDs must be UUIDs") from None
            parent_side = self._side(raw.get("parent_side"))
            child_side = self._side(raw.get("child_side"))
            if (parent_id, parent_side) == (child_id, child_side):
                raise ValidationError("Breakout endpoints must be distinct")
            loss = self._finite(raw.get("loss_db", 0.0), name="Breakout loss", low=0, high=10)
            parent, parent_cable = self._strand(parent_id)
            child, child_cable = self._strand(child_id)
            if parent_cable.project_id != project_id or child_cable.project_id != project_id:
                raise AuthorizationError("Breakout legs must stay within the breakout project")
            for endpoint in ((parent.id, parent_side), (child.id, child_side)):
                if endpoint in endpoint_set or self._endpoint_claim(*endpoint) is not None:
                    raise ConflictError("Breakout endpoint is already occupied")
            path = self._walk(parent.id, parent_side, self.MAX_TRACE_HOPS, project_id=project_id)
            if path["termination"] in {"cycle", "limit"}:
                raise ConflictError("Cannot safely add breakout to cyclic or bounded topology")
            if any(step.get("strand_id") == str(child.id) for step in path["steps"]):
                raise ConflictError("Breakout would create a fiber cycle")
            label = raw.get("label", f"Leg {number}")
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 180:
                raise ValidationError("Breakout leg label must contain 1 to 180 characters")
            leg = FiberBreakoutLeg(
                tenant_id=self.tenant_id,
                breakout_id=breakout.id,
                leg_number=number,
                label=label.strip(),
                parent_strand_id=parent.id,
                parent_side=parent_side,
                child_strand_id=child.id,
                child_side=child_side,
                loss_db=loss,
            )
            self.db.add(leg)
            self.db.flush()
            self.db.add_all([
                FiberEndpointClaim(
                    tenant_id=self.tenant_id,
                    strand_id=parent.id,
                    side=parent_side,
                    owner_type="breakout_leg",
                    owner_id=leg.id,
                ),
                FiberEndpointClaim(
                    tenant_id=self.tenant_id,
                    strand_id=child.id,
                    side=child_side,
                    owner_type="breakout_leg",
                    owner_id=leg.id,
                ),
            ])
            self.db.flush()
            endpoint_set.update({(parent.id, parent_side), (child.id, child_side)})
            rows.append(leg)
        self._audit("breakout.created", breakout, project_id, after={
            "identifier": identifier,
            "mode": mode,
            "leg_count": len(rows),
        })
        return self.get_breakout(breakout.id, action="write")

    def get_breakout(self, breakout_id: uuid.UUID, *, action: str = "read") -> dict[str, Any]:
        breakout = self._get(FiberBreakout, breakout_id)
        device = self._get(Device, breakout.device_id)
        self._authorize(action, breakout.project_id, device.location_id)
        legs = self.db.scalars(select(FiberBreakoutLeg).where(
            FiberBreakoutLeg.tenant_id == self.tenant_id,
            FiberBreakoutLeg.breakout_id == breakout.id,
            FiberBreakoutLeg.deleted_at.is_(None),
        ).order_by(FiberBreakoutLeg.leg_number).limit(576)).all()
        return {
            "id": str(breakout.id),
            "version": breakout.version,
            "project_id": str(breakout.project_id),
            "device_id": str(breakout.device_id),
            "identifier": breakout.identifier,
            "name": breakout.name,
            "mode": breakout.mode,
            "legs": [{
                "id": str(leg.id),
                "leg_number": leg.leg_number,
                "label": leg.label,
                "parent_strand_id": str(leg.parent_strand_id),
                "parent_side": leg.parent_side,
                "child_strand_id": str(leg.child_strand_id),
                "child_side": leg.child_side,
                "loss_db": leg.loss_db,
            } for leg in legs],
        }

    def release_breakout(self, breakout_id: uuid.UUID, expected_version: int) -> dict[str, Any]:
        breakout = self._get(FiberBreakout, breakout_id)
        device = self._get(Device, breakout.device_id)
        self._authorize("write", breakout.project_id, device.location_id)
        self._project_lock(breakout.project_id)
        before = self.get_breakout(breakout.id, action="write")
        leg_ids = self.db.scalars(select(FiberBreakoutLeg.id).where(
            FiberBreakoutLeg.tenant_id == self.tenant_id,
            FiberBreakoutLeg.breakout_id == breakout.id,
            FiberBreakoutLeg.deleted_at.is_(None),
        )).all()
        now = utcnow()
        self._atomic_update(
            FiberBreakout,
            breakout.id,
            expected_version,
            "Breakout changed; reload it before retrying",
            deleted_at=now,
            updated_at=now,
        )
        if leg_ids:
            self.db.execute(delete(FiberEndpointClaim).where(
                FiberEndpointClaim.tenant_id == self.tenant_id,
                FiberEndpointClaim.owner_type == "breakout_leg",
                FiberEndpointClaim.owner_id.in_(leg_ids),
            ))
            self.db.execute(update(FiberBreakoutLeg).where(
                FiberBreakoutLeg.tenant_id == self.tenant_id,
                FiberBreakoutLeg.id.in_(leg_ids),
                FiberBreakoutLeg.deleted_at.is_(None),
            ).values(
                deleted_at=now,
                updated_at=now,
                version=FiberBreakoutLeg.version + 1,
            ))
        self._audit("breakout.released", breakout, breakout.project_id, before=before)
        return {
            "id": str(breakout.id),
            "version": self._version(expected_version) + 1,
            "released": True,
        }

"""FiberService mixin: FiberSpliceMixin."""
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

class FiberSpliceMixin:
    def splice(self, slot_id, left_strand_id, left_side, right_strand_id, right_side,
               expected_version, loss_db=0.0):
        if left_side not in ("A", "B") or right_side not in ("A", "B"):
            raise ValidationError("Strand sides must be A or B")
        if left_strand_id == right_strand_id:
            raise ValidationError("A strand cannot be spliced to itself")
        if isinstance(loss_db, bool) or not isinstance(loss_db, (int, float)) or \
                not math.isfinite(loss_db) or not 0 <= loss_db <= 10:
            raise ValidationError("Splice loss must be finite and between 0 and 10 dB")
        slot = self._get(FiberCassetteSlot, slot_id)
        cassette = self._cassette(slot.cassette_id, "write")
        self._project_lock(cassette.project_id)
        # Read AFTER the mutex; stale pre-lock views must not drive cycle checks.
        for strand_id in (left_strand_id, right_strand_id):
            _, cable = self._strand(strand_id)
            if cable.project_id != cassette.project_id:
                raise AuthorizationError("Splices must stay within the cassette project")
        for side in ("A", "B"):
            path = self._walk(left_strand_id, side, self.MAX_TRACE_HOPS,
                              project_id=cassette.project_id, cassette_scope=cassette)
            if path["termination"] in {"cycle", "limit"}:
                raise ConflictError("Cannot safely splice a cyclic or truncated topology")
            if any(step.get("strand_id") == str(right_strand_id) for step in path["steps"]):
                raise ConflictError("Splice would create a fiber cycle")
        self._advance_slot(slot, expected_version)
        splice = FiberSplice(tenant_id=self.tenant_id, slot_id=slot.id, loss_db=float(loss_db))
        self.db.add(splice)
        self.db.flush()  # Unique active-slot constraint, including concurrent writers.
        self.db.add_all([
            FiberSpliceEnd(tenant_id=self.tenant_id, splice_id=splice.id,
                           strand_id=left_strand_id, side=left_side, end_number=1),
            FiberSpliceEnd(tenant_id=self.tenant_id, splice_id=splice.id,
                           strand_id=right_strand_id, side=right_side, end_number=2),
            FiberEndpointClaim(tenant_id=self.tenant_id, strand_id=left_strand_id,
                               side=left_side, owner_type="splice", owner_id=splice.id),
            FiberEndpointClaim(tenant_id=self.tenant_id, strand_id=right_strand_id,
                               side=right_side, owner_type="splice", owner_id=splice.id),
        ])
        self.db.flush()  # Unified claims prevent splice/termination/breakout overlap.
        ends = [{"strand_id": str(left_strand_id), "side": left_side},
                {"strand_id": str(right_strand_id), "side": right_side}]
        self._audit("splice.created", splice, cassette.project_id,
                    after={"slot_id": str(slot.id), "ends": ends, "loss_db": float(loss_db)})
        return {"id": str(splice.id), "slot_id": str(slot.id),
                "slot_version": expected_version + 1, "ends": ends, "loss_db": float(loss_db)}

    def release(self, slot_id, expected_version):
        slot = self._get(FiberCassetteSlot, slot_id)
        cassette = self._cassette(slot.cassette_id, "write")
        self._project_lock(cassette.project_id)
        splice = self.db.scalar(select(FiberSplice).where(
            FiberSplice.tenant_id == self.tenant_id, FiberSplice.slot_id == slot.id,
            FiberSplice.deleted_at.is_(None),
        ).execution_options(populate_existing=True))
        if splice is None:
            raise ConflictError("Slot has no active splice")
        self._advance_slot(slot, expected_version)
        ends = self.db.scalars(select(FiberSpliceEnd).where(
            FiberSpliceEnd.tenant_id == self.tenant_id, FiberSpliceEnd.splice_id == splice.id,
        ).order_by(FiberSpliceEnd.end_number)).all()
        before = {"ends": [{"strand_id": str(e.strand_id), "side": e.side} for e in ends],
                  "slot_id": str(slot.id), "loss_db": splice.loss_db}
        self.db.execute(delete(FiberEndpointClaim).where(
            FiberEndpointClaim.tenant_id == self.tenant_id,
            FiberEndpointClaim.owner_type == "splice",
            FiberEndpointClaim.owner_id == splice.id,
        ))
        self.db.execute(delete(FiberSpliceEnd).where(
            FiberSpliceEnd.tenant_id == self.tenant_id, FiberSpliceEnd.splice_id == splice.id,
        ))
        # Preserve the splice row and audited end identities; only active claims are removed.
        splice.deleted_at = utcnow()
        self.db.flush()
        self._audit("splice.released", splice, cassette.project_id, before=before)
        return {"slot_id": str(slot.id), "slot_version": expected_version + 1,
                "released_splice_id": str(splice.id)}

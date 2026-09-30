"""FiberService mixin: FiberCoreMixin."""
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

class FiberCoreMixin:
    def __init__(self, session: Session, principal: Principal):
        self.db = session
        self.principal = principal
        self.tenant_id = principal.tenant_id
        if session.info.get("bypass_tenant"):
            raise AuthorizationError("Fiber operations cannot use a platform bypass session")
        if session.info.get("tenant_id") != self.tenant_id:
            raise AuthorizationError("Fiber operations require a matching tenant session")

    def _get(self, model, object_id):
        # Explicit predicates also protect already-cached cross-tenant identities.
        obj = self.db.scalar(select(model).where(
            model.id == object_id, model.tenant_id == self.tenant_id,
            model.deleted_at.is_(None),
        ).execution_options(populate_existing=True))
        if obj is None:
            raise NotFoundError("Fiber resource or parent not found")
        return obj

    def _authorize(self, action, project_id, location_id=None):
        tenant = self.db.scalar(select(Tenant).where(
            Tenant.id == self.tenant_id, Tenant.active.is_(True),
        ))
        if tenant is None:
            raise AuthorizationError("Tenant is inactive or unavailable")
        self._get(Project, project_id)
        if location_id is not None:
            self._get(Location, location_id)
        # Never treat request-supplied project/location headers as object authority.
        actual = resolve_principal(
            self.db, actor_id=self.principal.actor_id, tenant_id=self.tenant_id,
            project_id=project_id, location_id=location_id,
            request_id=self.principal.request_id, ip_address=self.principal.ip_address,
            user_agent=self.principal.user_agent,
        )
        require_permission(actual, f"fiber:{action}")
        return actual

    def _project_lock(self, project_id):
        # A no-op UPDATE provides a cross-dialect write mutex without changing the
        # project's revision/timestamp. All splice/release writers use this lock.
        table = Project.__table__
        result = self.db.execute(table.update().where(
            table.c.id == project_id, table.c.tenant_id == self.tenant_id,
            table.c.deleted_at.is_(None),
        ).values(version=table.c.version, updated_at=table.c.updated_at))
        if result.rowcount != 1:
            raise NotFoundError("Project not found")

    def _audit(self, action, obj, project_id, before=None, after=None):
        record_audit(self.db, principal=self.principal, action=f"fiber.{action}",
                     object_type=obj.__tablename__, object_id=obj.id,
                     before=before, after=after, project_id=project_id)

    @staticmethod
    def _name(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 180:
            raise ValidationError("Name must contain 1 to 180 characters")
        return value.strip()

    def _cable(self, cable_id):
        cable = self._get(Cable, cable_id)
        if cable.project_id is None:
            raise ValidationError("Assign the cable to a project before provisioning fiber")
        if not (cable.media_type.lower().startswith("fiber") or
                cable.media_type.lower() in {"os1", "os2", "om1", "om2", "om3", "om4", "om5"}):
            raise ValidationError("Cable is not fiber media")
        return cable

    def provision_bundle(self, cable_id: uuid.UUID, name: str) -> dict[str, Any]:
        cable = self._cable(cable_id)
        self._authorize("write", cable.project_id)
        name = self._name(name)
        count = cable.strand_count
        if type(count) is not int or not 1 <= count <= 576:
            raise ValidationError("Cable strand_count must be between 1 and 576")
        bundle = FiberBundle(tenant_id=self.tenant_id, cable_id=cable.id,
                             name=name, strand_count=count)
        self.db.add(bundle)
        self.db.flush()
        self.db.add_all(FiberStrand(tenant_id=self.tenant_id, bundle_id=bundle.id, number=n)
                        for n in range(1, count + 1))
        self.db.flush()
        self._audit("bundle.created", bundle, cable.project_id, after={"strand_count": count})
        return self.get_bundle(bundle.id, action="write")

    def get_bundle(self, bundle_id, *, action="read"):
        bundle = self._get(FiberBundle, bundle_id)
        cable = self._cable(bundle.cable_id)
        self._authorize(action, cable.project_id)
        strands = self.db.scalars(select(FiberStrand).where(
            FiberStrand.tenant_id == self.tenant_id, FiberStrand.bundle_id == bundle.id,
            FiberStrand.deleted_at.is_(None),
        ).order_by(FiberStrand.number).limit(576)).all()
        return {"id": str(bundle.id), "cable_id": str(cable.id), "name": bundle.name,
                "version": bundle.version, "strand_count": bundle.strand_count,
                "strands": [{"id": str(s.id), "number": s.number} for s in strands]}

    def create_cassette(self, device_id, project_id, name, slot_count):
        device = self._get(Device, device_id)
        self._authorize("write", project_id, device.location_id)
        name = self._name(name)
        if type(slot_count) is not int or not 1 <= slot_count <= 288:
            raise ValidationError("Cassette slot_count must be between 1 and 288")
        cassette = FiberCassette(tenant_id=self.tenant_id, device_id=device.id,
                                project_id=project_id, name=name, slot_count=slot_count)
        self.db.add(cassette)
        self.db.flush()
        self.db.add_all(FiberCassetteSlot(tenant_id=self.tenant_id,
                         cassette_id=cassette.id, number=n) for n in range(1, slot_count + 1))
        self.db.flush()
        self._audit("cassette.created", cassette, project_id, after={"slot_count": slot_count})
        return self.get_cassette(cassette.id, action="write")

    def _cassette(self, cassette_id, action):
        cassette = self._get(FiberCassette, cassette_id)
        device = self._get(Device, cassette.device_id)
        self._authorize(action, cassette.project_id, device.location_id)
        return cassette

    def get_cassette(self, cassette_id, *, action="read"):
        cassette = self._cassette(cassette_id, action)
        rows = self.db.execute(select(FiberCassetteSlot, FiberSplice).outerjoin(
            FiberSplice, (FiberSplice.slot_id == FiberCassetteSlot.id) &
            (FiberSplice.tenant_id == self.tenant_id) & FiberSplice.deleted_at.is_(None),
        ).where(FiberCassetteSlot.tenant_id == self.tenant_id,
                FiberCassetteSlot.cassette_id == cassette.id,
                FiberCassetteSlot.deleted_at.is_(None))
          .order_by(FiberCassetteSlot.number).limit(288)).all()
        return {"id": str(cassette.id), "name": cassette.name,
                "device_id": str(cassette.device_id), "project_id": str(cassette.project_id),
                "slot_count": cassette.slot_count,
                "slots": [{"id": str(slot.id), "number": slot.number, "version": slot.version,
                           "splice_id": str(splice.id) if splice else None,
                           "loss_db": splice.loss_db if splice else None}
                          for slot, splice in rows]}

    def _strand(self, strand_id):
        strand = self._get(FiberStrand, strand_id)
        bundle = self._get(FiberBundle, strand.bundle_id)
        return strand, self._cable(bundle.cable_id)

    def _advance_slot(self, slot, expected_version):
        if type(expected_version) is not int or expected_version < 1:
            raise ValidationError("A positive expected_version is required")
        result = self.db.execute(update(FiberCassetteSlot).where(
            FiberCassetteSlot.id == slot.id,
            FiberCassetteSlot.tenant_id == self.tenant_id,
            FiberCassetteSlot.deleted_at.is_(None),
            FiberCassetteSlot.version == expected_version,
        ).values(version=FiberCassetteSlot.version + 1, updated_at=utcnow()))
        if result.rowcount != 1:
            raise ConflictError("Slot changed; reload it before retrying")

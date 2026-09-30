"""Advanced Fiber mixin: FiberAdvancedBaseMixin."""
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

class FiberAdvancedBaseMixin:
    @staticmethod
    def _finite(value: Any, *, name: str, low: float, high: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"{name} must be numeric")
        result = float(value)
        if not math.isfinite(result) or not low <= result <= high:
            raise ValidationError(f"{name} must be between {low} and {high}")
        return result

    @staticmethod
    def _identifier(value: Any) -> str:
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 180:
            raise ValidationError("Identifier must contain 1 to 180 characters")
        return value.strip()

    @staticmethod
    def _text(value: Any, *, name: str, max_length: int, required: bool = True) -> str | None:
        if value is None and not required:
            return None
        if not isinstance(value, str):
            raise ValidationError(f"{name} must be text")
        clean = value.strip()
        if (required and not clean) or len(clean) > max_length:
            qualifier = f"1 to {max_length}" if required else f"0 to {max_length}"
            raise ValidationError(f"{name} must contain {qualifier} characters")
        return clean or None

    @staticmethod
    def _side(value: Any) -> str:
        if value not in ("A", "B"):
            raise ValidationError("Strand side must be A or B")
        return value

    @staticmethod
    def _media_family(value: str) -> str:
        lowered = value.lower()
        if any(token in lowered for token in ("cat", "copper", "rj45", "punchdown")):
            return "copper"
        if any(token in lowered for token in ("fiber", "os", "om", "lc", "sc", "mpo", "mtp")):
            return "fiber"
        return lowered

    def _port(self, port_id: uuid.UUID) -> tuple[Port, Device]:
        port = self._get(Port, port_id)
        device = self._get(Device, port.device_id)
        return port, device

    def _copper_cable(self, cable_id: uuid.UUID) -> Cable:
        cable = self._get(Cable, cable_id)
        if cable.project_id is None:
            raise ValidationError("Assign the cable to a project before provisioning pairs")
        if self._media_family(cable.media_type) != "copper":
            raise ValidationError("Cable is not copper media")
        return cable

    def _endpoint_claim(self, strand_id: uuid.UUID, side: str):
        claim = self.db.scalar(select(FiberEndpointClaim).where(
            FiberEndpointClaim.tenant_id == self.tenant_id,
            FiberEndpointClaim.strand_id == strand_id,
            FiberEndpointClaim.side == side,
            FiberEndpointClaim.deleted_at.is_(None),
        ))
        if claim is not None:
            return claim
        # Safety for migrated/corrupt pre-005 data before repair.
        legacy = self.db.scalar(select(FiberSpliceEnd).where(
            FiberSpliceEnd.tenant_id == self.tenant_id,
            FiberSpliceEnd.strand_id == strand_id,
            FiberSpliceEnd.side == side,
            FiberSpliceEnd.deleted_at.is_(None),
        ))
        return legacy

    def _port_claim(self, port_id: uuid.UUID):
        claim = self.db.scalar(select(PhysicalPortClaim).where(
            PhysicalPortClaim.tenant_id == self.tenant_id,
            PhysicalPortClaim.port_id == port_id,
            PhysicalPortClaim.deleted_at.is_(None),
        ))
        if claim is not None:
            return claim
        return self.db.scalar(select(CableTermination).where(
            CableTermination.tenant_id == self.tenant_id,
            CableTermination.port_id == port_id,
            CableTermination.deleted_at.is_(None),
        ))

    @staticmethod
    def _version(expected_version: Any) -> int:
        if type(expected_version) is not int or expected_version < 1:
            raise ValidationError("A positive expected_version is required")
        return expected_version

    def _atomic_update(self, model, object_id: uuid.UUID, expected_version: int,
                       message: str, **values: Any) -> None:
        expected_version = self._version(expected_version)
        now = values.pop("updated_at", utcnow())
        result = self.db.execute(update(model).where(
            model.id == object_id,
            model.tenant_id == self.tenant_id,
            model.deleted_at.is_(None),
            model.version == expected_version,
        ).values(version=model.version + 1, updated_at=now, **values))
        if result.rowcount != 1:
            raise ConflictError(message)

    def terminate_strand(
        self,
        *,
        strand_id: uuid.UUID,
        side: str,
        port_id: uuid.UUID,
        connection_type: str = "connector",
        loss_db: float = 0.0,
    ) -> dict[str, Any]:
        side = self._side(side)
        if connection_type not in {"connector", "pigtail", "fusion", "mechanical"}:
            raise ValidationError("Unsupported fiber termination type")
        loss = self._finite(loss_db, name="Termination loss", low=0, high=10)
        strand, cable = self._strand(strand_id)
        port, device = self._port(port_id)
        if cable.project_id is None:
            raise ValidationError("Fiber cable has no project")
        self._authorize("write", cable.project_id, device.location_id)
        if self._media_family(port.media_type) != "fiber":
            raise ValidationError("Fiber strand requires a fiber-compatible port")
        self._project_lock(cable.project_id)
        if self._endpoint_claim(strand.id, side) is not None:
            raise ConflictError("Fiber endpoint is already occupied")
        if self._port_claim(port.id) is not None:
            raise ConflictError("Physical port is already occupied")
        termination = FiberPortTermination(
            tenant_id=self.tenant_id,
            project_id=cable.project_id,
            strand_id=strand.id,
            side=side,
            port_id=port.id,
            connection_type=connection_type,
            loss_db=loss,
        )
        self.db.add(termination)
        self.db.flush()
        self.db.add_all([
            FiberEndpointClaim(
                tenant_id=self.tenant_id,
                strand_id=strand.id,
                side=side,
                owner_type="fiber_termination",
                owner_id=termination.id,
            ),
            PhysicalPortClaim(
                tenant_id=self.tenant_id,
                port_id=port.id,
                owner_type="fiber_termination",
                owner_id=termination.id,
            ),
        ])
        self.db.flush()
        payload = {
            "strand_id": str(strand.id),
            "side": side,
            "port_id": str(port.id),
            "connection_type": connection_type,
            "loss_db": loss,
        }
        self._audit("termination.created", termination, cable.project_id, after=payload)
        return {"id": str(termination.id), "version": termination.version, **payload}

    def release_termination(
        self, termination_id: uuid.UUID, expected_version: int
    ) -> dict[str, Any]:
        termination = self._get(FiberPortTermination, termination_id)
        strand, cable = self._strand(termination.strand_id)
        port, device = self._port(termination.port_id)
        if cable.project_id != termination.project_id:
            raise ConflictError("Termination project does not match its strand cable")
        self._authorize("write", termination.project_id, device.location_id)
        self._project_lock(termination.project_id)
        before = {
            "strand_id": str(strand.id),
            "side": termination.side,
            "port_id": str(port.id),
            "connection_type": termination.connection_type,
            "loss_db": termination.loss_db,
        }
        now = utcnow()
        self._atomic_update(
            FiberPortTermination,
            termination.id,
            expected_version,
            "Termination changed; reload it before retrying",
            deleted_at=now,
            updated_at=now,
        )
        self.db.execute(delete(FiberEndpointClaim).where(
            FiberEndpointClaim.tenant_id == self.tenant_id,
            FiberEndpointClaim.owner_type == "fiber_termination",
            FiberEndpointClaim.owner_id == termination.id,
        ))
        self.db.execute(delete(PhysicalPortClaim).where(
            PhysicalPortClaim.tenant_id == self.tenant_id,
            PhysicalPortClaim.owner_type == "fiber_termination",
            PhysicalPortClaim.owner_id == termination.id,
        ))
        self._audit("termination.released", termination, termination.project_id, before=before)
        return {
            "id": str(termination.id),
            "version": self._version(expected_version) + 1,
            "released": True,
        }

    def provision_pairs(self, cable_id: uuid.UUID) -> dict[str, Any]:
        cable = self._copper_cable(cable_id)
        self._authorize("write", cable.project_id)
        count = cable.pair_count
        if type(count) is not int or not 1 <= count <= self.MAX_MEMBERS:
            raise ValidationError("Cable pair_count must be between 1 and 600")
        existing = self.db.scalar(select(CopperPair.id).where(
            CopperPair.tenant_id == self.tenant_id,
            CopperPair.cable_id == cable.id,
            CopperPair.deleted_at.is_(None),
        ))
        if existing is not None:
            raise ConflictError("Cable pairs are already provisioned")
        pairs = [
            CopperPair(tenant_id=self.tenant_id, cable_id=cable.id, number=number)
            for number in range(1, count + 1)
        ]
        self.db.add_all(pairs)
        self.db.flush()
        self._audit("pairs.provisioned", pairs[0], cable.project_id, after={
            "cable_id": str(cable.id), "pair_count": count,
        })
        return {
            "cable_id": str(cable.id),
            "pair_count": count,
            "pairs": [{"id": str(pair.id), "number": pair.number} for pair in pairs],
        }

    def get_pairs(self, cable_id: uuid.UUID) -> dict[str, Any]:
        cable = self._copper_cable(cable_id)
        self._authorize("read", cable.project_id)
        pairs = self.db.scalars(select(CopperPair).where(
            CopperPair.tenant_id == self.tenant_id,
            CopperPair.cable_id == cable.id,
            CopperPair.deleted_at.is_(None),
        ).order_by(CopperPair.number).limit(self.MAX_MEMBERS)).all()
        if not pairs:
            raise NotFoundError("Cable has no provisioned copper pairs")
        return {
            "cable_id": str(cable.id),
            "pair_count": len(pairs),
            "pairs": [{"id": str(pair.id), "number": pair.number,
                       "color_code": pair.color_code} for pair in pairs],
        }

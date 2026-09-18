"""Scoped, versioned retirement of saved cables, preserving historical rows.

Lock order is project then cable. Bundle/pair and work-order creation share this
boundary. Fiber topology writers already serialize on the project; cable workflow
writers lock only the cable. No caller may acquire project after cable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.fiber_models import CopperPair, FiberBundle, FiberStrand, PhysicalPortClaim
from app.models import (
    Cable,
    CableRouteSegment,
    CableStatus,
    CableTermination,
    Device,
    Port,
    Project,
    WorkOrder,
    WorkOrderStatus,
)
from app.schemas_cable_lifecycle import (
    CableDeletionPreview,
    CableEndpointPreview,
    CableLifecycleResult,
    LifecycleBlocker,
)
from app.security import Principal
from app.services.resource_scope import ResourceScope


def lock_active_cable(session: Session, principal: Principal, cable_id: uuid.UUID) -> Cable:
    """Take the shared lifecycle mutex; callers authorize before and after waiting."""
    conditions = (
        Cable.id == cable_id,
        Cable.tenant_id == principal.tenant_id,
        Cable.deleted_at.is_(None),
    )
    cable = session.scalar(select(Cable).where(*conditions))
    if cable is None:
        raise NotFoundError("Cable not found in tenant")
    project_id = cable.project_id
    session.flush()
    if project_id is not None:
        table = Project.__table__
        result = session.execute(
            table.update()
            .where(
                table.c.id == project_id,
                table.c.tenant_id == principal.tenant_id,
                table.c.deleted_at.is_(None),
            )
            .values(version=table.c.version, updated_at=table.c.updated_at)
        )
        if result.rowcount != 1:
            raise NotFoundError("Cable project not found in tenant")
    if session.get_bind().dialect.name == "sqlite":
        session.execute(
            update(Cable)
            .where(*conditions)
            .values(
                version=Cable.version,
                updated_at=Cable.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
    cable = session.scalar(
        select(Cable).where(*conditions).with_for_update().execution_options(populate_existing=True)
    )
    if cable is None:
        raise NotFoundError("Cable not found in tenant")
    if cable.project_id != project_id:
        raise ConflictError("Cable project changed; reload before retrying")
    if cable.installation_status == CableStatus.REMOVED:
        raise ConflictError("Cable is already removed")
    session.expire_all()
    return cable


class CableLifecycleService:
    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.principal = principal

    def _active(self, model):
        return select(model).where(
            model.tenant_id == self.principal.tenant_id,
            model.deleted_at.is_(None),
        )

    def _blockers(self, cable: Cable) -> list[LifecycleBlocker]:
        blockers = []
        if cable.installation_status == CableStatus.REMOVED:
            blockers.append(
                LifecycleBlocker(
                    code="already_removed",
                    message="该线缆已经拆除，不能再次操作。",
                )
            )
        # Provisioned topology is deliberately not cascaded in V1. This also protects
        # splice/channel/breakout references without disclosing out-of-scope objects.
        bundle = self.session.scalar(
            self._active(FiberBundle).where(FiberBundle.cable_id == cable.id).limit(1)
        )
        strand = self.session.scalar(
            select(FiberStrand.id)
            .join(
                FiberBundle,
                and_(
                    FiberBundle.id == FiberStrand.bundle_id,
                    FiberBundle.tenant_id == self.principal.tenant_id,
                ),
            )
            .where(
                FiberStrand.tenant_id == self.principal.tenant_id,
                FiberStrand.deleted_at.is_(None),
                FiberBundle.cable_id == cable.id,
            )
            .execution_options(skip_tenant_criteria=True)
            .limit(1)
        )
        if bundle is not None or strand is not None:
            blockers.append(
                LifecycleBlocker(
                    code="fiber_topology",
                    message="已登记光纤束或纤芯拓扑；当前版本不支持连同拓扑拆除。请先处理关联。",
                )
            )
        if (
            self.session.scalar(
                self._active(CopperPair).where(CopperPair.cable_id == cable.id).limit(1)
            )
            is not None
        ):
            blockers.append(
                LifecycleBlocker(
                    code="copper_topology",
                    message="已登记铜缆线对或通道拓扑；当前版本不支持连同拓扑拆除。请先处理关联。",
                )
            )
        if (
            self.session.scalar(
                self._active(WorkOrder)
                .where(
                    WorkOrder.cable_id == cable.id,
                    WorkOrder.status.not_in([WorkOrderStatus.COMPLETED, WorkOrderStatus.CANCELLED]),
                )
                .limit(1)
            )
            is not None
        ):
            blockers.append(
                LifecycleBlocker(
                    code="active_work_order",
                    message="存在尚未完成的关联工单，请先完成或取消工单。",
                )
            )
        return blockers

    def preview(self, cable_id: uuid.UUID) -> CableDeletionPreview:
        scope = ResourceScope(self.session, self.principal)
        cable = scope.get(Cable, cable_id)
        scope.require(cable, "cable:read")
        action = "delete" if cable.installation_status == CableStatus.PLANNED else "remove"
        blockers = self._blockers(cable)
        try:
            scope.require(cable, f"cable:{action}")
        except AuthorizationError:
            blockers.insert(
                0,
                LifecycleBlocker(
                    code="permission_denied",
                    message="当前账号没有此线缆及其完整范围的删除或拆除权限。",
                ),
            )
        endpoints = self.session.execute(
            select(CableTermination.side, Device.name, Port.label)
            .select_from(CableTermination)
            .outerjoin(
                Port,
                and_(
                    Port.id == CableTermination.port_id,
                    Port.tenant_id == self.principal.tenant_id,
                    Port.deleted_at.is_(None),
                ),
            )
            .outerjoin(
                Device,
                and_(
                    Device.id == Port.device_id,
                    Device.tenant_id == self.principal.tenant_id,
                    Device.deleted_at.is_(None),
                ),
            )
            .where(
                CableTermination.tenant_id == self.principal.tenant_id,
                CableTermination.cable_id == cable.id,
                CableTermination.deleted_at.is_(None),
            )
            .order_by(CableTermination.side)
        ).all()
        route_count = self.session.scalar(
            select(func.count())
            .select_from(CableRouteSegment)
            .where(
                CableRouteSegment.tenant_id == self.principal.tenant_id,
                CableRouteSegment.cable_id == cable.id,
                CableRouteSegment.deleted_at.is_(None),
            )
        )
        return CableDeletionPreview(
            id=cable.id,
            identifier=cable.identifier,
            version=cable.version,
            status=cable.installation_status,
            action=action,
            allowed=not blockers,
            blockers=blockers,
            endpoints=[
                CableEndpointPreview(side=side, device_name=device, port_label=port)
                for side, device, port in endpoints
            ],
            route_segment_count=route_count or 0,
        )

    def retire(
        self,
        cable_id: uuid.UUID,
        *,
        action: Literal["delete", "remove"],
        expected_version: int,
        reason: str | None = None,
    ) -> CableLifecycleResult:
        if action not in {"delete", "remove"}:
            raise ValidationError("Unsupported cable lifecycle action")
        if type(expected_version) is not int or expected_version < 1:
            raise ValidationError("A positive expected_version is required")
        if action == "remove" and (
            not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000
        ):
            raise ValidationError("Removal reason must contain 1 to 1000 characters")
        permission = f"cable:{action}"
        scope = ResourceScope(self.session, self.principal)
        cable = scope.get(Cable, cable_id)
        scope.require(cable, permission)
        cable = lock_active_cable(self.session, self.principal, cable.id)
        scope = ResourceScope(self.session, self.principal)
        scope.require(cable, permission)
        if cable.version != expected_version:
            raise ConflictError("Cable changed; reload its deletion preview before retrying")
        expected_action = "delete" if cable.installation_status == CableStatus.PLANNED else "remove"
        if action != expected_action:
            raise ConflictError("Cable status requires a different action; reload its preview")
        blockers = self._blockers(cable)
        if blockers:
            raise ConflictError(" ".join(blocker.message for blocker in blockers))
        terminations = list(
            self.session.scalars(
                self._active(CableTermination).where(CableTermination.cable_id == cable.id)
            )
        )
        routes = list(
            self.session.scalars(
                self._active(CableRouteSegment).where(CableRouteSegment.cable_id == cable.id)
            )
        )
        # Never release by port ID: a stale request must not free a replacement cable.
        claims = list(
            self.session.scalars(
                self._active(PhysicalPortClaim).where(
                    PhysicalPortClaim.owner_type == "cable_termination",
                    PhysicalPortClaim.owner_id.in_([row.id for row in terminations]),
                )
            )
        )
        before = {
            "identifier": cable.identifier,
            "status": cable.installation_status.value,
            "version": cable.version,
        }
        now = datetime.now(UTC)
        for row in [cable, *terminations, *routes, *claims]:
            row.deleted_at = now
        if action == "remove":
            cable.installation_status = CableStatus.REMOVED
        record_audit(
            self.session,
            principal=scope.principal,
            action=f"cable.{action}d",
            object_type="cable",
            object_id=cable.id,
            before=before,
            after={
                "status": cable.installation_status.value,
                "version": expected_version + 1,
                "deleted_at": now.isoformat(),
                "reason": reason.strip() if reason else None,
                "released_endpoints": len(terminations),
                "released_routes": len(routes),
            },
            project_id=cable.project_id,
        )
        return CableLifecycleResult(id=cable.id, action=action, version=cable.version)

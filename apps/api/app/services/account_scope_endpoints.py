"""Scope checks for catalogs, access delegation and work-order references."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.models import (
    AccessGrant,
    Cable,
    CableTermination,
    Device,
    DeviceTemplate,
    Location,
    Organization,
    Port,
    Project,
    Tenant,
    UserIdentity,
    WorkOrder,
    WorkOrderStatus,
)
from app.schemas import AccessGrantCreate, WorkOrderCreate
from app.security import Principal, require_permission, resolve_principal
from app.services.cable_lifecycle import lock_active_cable
from app.services.resource_scope import ResourceScope
from app.services.workspace_lock import lock_workspace


class AccountScopeEndpoints:
    def __init__(self, db: Session, principal: Principal):
        self.db = db
        self.scope = ResourceScope(db, principal)
        self.principal = self.scope.principal

    def require_member(self, permission: str) -> None:
        require_permission(self.principal, permission)
        if not self.principal.is_tenant_member:
            raise AuthorizationError("This tenant-wide operation requires workspace membership")

    def templates(self) -> list[DeviceTemplate]:
        require_permission(self.principal, "device:read")
        statement = (
            select(DeviceTemplate)
            .where(
                DeviceTemplate.tenant_id == self.principal.tenant_id,
                DeviceTemplate.deleted_at.is_(None),
            )
            .order_by(DeviceTemplate.manufacturer, DeviceTemplate.model)
        )
        if not self.principal.is_tenant_member:
            devices = self.db.scalars(
                select(Device).where(
                    Device.tenant_id == self.principal.tenant_id,
                    Device.deleted_at.is_(None),
                )
            )
            template_ids = {
                item.template_id for item in self.scope.iter_visible(devices, "device:read")
            }
            statement = statement.where(DeviceTemplate.id.in_(template_ids))
        return list(self.db.scalars(statement))

    def work_order(self, payload: WorkOrderCreate) -> WorkOrder:
        require_permission(self.principal, "work_order:create")
        project = self.scope.get(Project, payload.project_id)
        if payload.location_id:
            self.scope.get(Location, payload.location_id)
        if payload.cable_id:
            cable = self.scope.get(Cable, payload.cable_id)
            self.scope.require(cable, "work_order:create")
            cable = lock_active_cable(self.db, self.principal, cable.id)
            self.scope = ResourceScope(self.db, self.principal)
            self.principal = self.scope.principal
            self.scope.require(cable, "work_order:create")
            if cable.project_id != project.id:
                raise ValidationError("Work order and cable must belong to the same project")
        organization = self.db.get(Organization, payload.assigned_organization_id)
        if organization is None:
            raise NotFoundError("Assigned organization not found")
        order = WorkOrder(
            tenant_id=self.principal.tenant_id,
            status=WorkOrderStatus.READY,
            created_by=self.principal.actor_id,
            **payload.model_dump(),
        )
        if payload.location_id is not None or payload.cable_id is None:
            self.scope.require_location("work_order:create", project.id, payload.location_id)
        self.scope.require(order, "work_order:create")
        if payload.assigned_user_id:
            actor = self.db.get(UserIdentity, payload.assigned_user_id)
            if not actor or not actor.active or actor.organization_id != organization.id:
                raise ValidationError(
                    "Assigned account must be active and belong to the assigned organization"
                )
            location_id = payload.location_id
            if location_id is None and payload.cable_id:
                location_id = self.db.scalar(
                    select(Device.location_id)
                    .join(
                        Port,
                        Port.device_id == Device.id,
                    )
                    .join(CableTermination, CableTermination.port_id == Port.id)
                    .where(
                        CableTermination.cable_id == payload.cable_id,
                        CableTermination.tenant_id == self.principal.tenant_id,
                        CableTermination.deleted_at.is_(None),
                        Port.tenant_id == self.principal.tenant_id,
                        Port.deleted_at.is_(None),
                        Device.tenant_id == self.principal.tenant_id,
                        Device.deleted_at.is_(None),
                    )
                    .limit(1)
                )
            assignee = resolve_principal(
                self.db,
                actor_id=actor.id,
                tenant_id=self.principal.tenant_id,
                project_id=project.id,
                location_id=location_id,
            )
            ResourceScope(self.db, assignee).require(order, "work_order:read")
        else:
            tenant = self.db.get(Tenant, self.principal.tenant_id)
            if organization.id not in {
                project.customer_organization_id,
                project.contractor_organization_id,
                tenant.owner_organization_id if tenant else None,
            }:
                raise ValidationError("Assigned organization is not a participant in this project")
        return order

    def _lock_grant_state(self, permission: str) -> Tenant:
        tenant = lock_workspace(self.db, self.principal.tenant_id)
        if tenant is None:
            raise NotFoundError("Workspace not found")
        # Membership may have changed while waiting for a sharing mutation to commit.
        self.scope = ResourceScope(self.db, self.principal)
        self.principal = self.scope.principal
        self.require_member(permission)
        return tenant

    def validate_grant(self, payload: AccessGrantCreate) -> None:
        tenant = self._lock_grant_state("access_grant:create")
        if tenant.workspace_kind == "personal":
            raise AuthorizationError("Personal workspaces cannot grant access to other accounts")
        self.scope.get(Project, payload.project_id)
        if payload.location_id:
            self.scope.get(Location, payload.location_id)
        organization = self.db.get(Organization, payload.subject_organization_id)
        if organization is None:
            raise NotFoundError("Subject organization not found")
        actor = (
            self.db.get(UserIdentity, payload.subject_user_id) if payload.subject_user_id else None
        )
        if not actor or not actor.active or actor.organization_id != organization.id:
            raise ValidationError(
                "An active subject account belonging to the subject organization is required"
            )
        if not payload.permissions or any(not value.strip() for value in payload.permissions):
            raise ValidationError("At least one non-empty permission is required")
        if any(not self.principal.can(permission) for permission in payload.permissions):
            raise AuthorizationError(
                "Cannot delegate permissions that the current account does not have"
            )

        def utc(value: datetime) -> datetime:
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

        if payload.expires_at:
            expires = utc(payload.expires_at)
            if expires <= datetime.now(UTC) or (
                payload.starts_at and expires <= utc(payload.starts_at)
            ):
                raise ValidationError(
                    "Access expiry must be in the future and after the start time"
                )

    def revocable_grant(self, grant_id) -> AccessGrant:
        self._lock_grant_state("access_grant:revoke")
        return self.scope.get(AccessGrant, grant_id)

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import AuthorizationError
from app.models import (
    AccessGrant,
    AccessGrantStatus,
    Location,
    Project,
    Tenant,
    TenantMembership,
    UserIdentity,
)


@dataclass(frozen=True)
class Principal:
    actor_id: uuid.UUID
    actor_organization_id: uuid.UUID
    tenant_id: uuid.UUID
    permissions: frozenset[str]
    role: str
    is_tenant_member: bool
    grant_ids: tuple[uuid.UUID, ...] = ()
    project_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None

    def can(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


def require_permission(principal: Principal, permission: str) -> None:
    if not principal.can(permission):
        raise AuthorizationError(f"Missing permission: {permission}")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def is_descendant_or_self(session: Session, candidate_id: uuid.UUID, scope_id: uuid.UUID) -> bool:
    scope = session.scalar(
        select(Location)
        .execution_options(populate_existing=True)
        .where(Location.id == scope_id, Location.deleted_at.is_(None))
    )
    if scope is None:
        return False
    current = session.scalar(
        select(Location)
        .execution_options(populate_existing=True)
        .where(
            Location.id == candidate_id,
            Location.tenant_id == scope.tenant_id,
            Location.deleted_at.is_(None),
        )
    )
    visited: set[uuid.UUID] = set()
    while current and current.id not in visited:
        if current.id == scope_id:
            return True
        visited.add(current.id)
        current = (
            session.scalar(
                select(Location)
                .execution_options(populate_existing=True)
                .where(
                    Location.id == current.parent_id,
                    Location.tenant_id == scope.tenant_id,
                    Location.deleted_at.is_(None),
                )
            )
            if current.parent_id
            else None
        )
    return False


def resolve_principal(
    session: Session,
    *,
    actor_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> Principal:
    actor = session.get(UserIdentity, actor_id, populate_existing=True)
    if not actor or not actor.active:
        raise AuthorizationError("Unknown or inactive identity")

    tenant = session.get(Tenant, tenant_id, populate_existing=True)
    if not tenant or not tenant.active:
        raise AuthorizationError("Unknown or inactive workspace")
    if tenant.workspace_kind == "personal" and tenant.workspace_owner_id != actor_id:
        raise AuthorizationError("Personal workspace is accessible only to its owner")

    # Workspace context is not evidence that a referenced project/location belongs here.
    for model, selected_id in ((Project, project_id), (Location, location_id)):
        if (
            selected_id is not None
            and session.scalar(
                select(model.id).where(
                    model.id == selected_id,
                    model.tenant_id == tenant_id,
                    model.deleted_at.is_(None),
                )
            )
            is None
        ):
            raise AuthorizationError("Requested project or location is outside this workspace")

    membership = session.scalar(
        select(TenantMembership)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == actor_id,
            TenantMembership.active.is_(True),
            TenantMembership.deleted_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )
    if membership:
        return Principal(
            actor_id=actor.id,
            actor_organization_id=actor.organization_id,
            tenant_id=tenant_id,
            permissions=frozenset(membership.permissions),
            role=membership.role,
            is_tenant_member=True,
            project_id=project_id,
            location_id=location_id,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    now = datetime.now(UTC)
    grants = session.scalars(
        select(AccessGrant)
        .where(
            AccessGrant.tenant_id == tenant_id,
            AccessGrant.subject_user_id == actor_id,
            AccessGrant.status == AccessGrantStatus.ACTIVE,
            AccessGrant.deleted_at.is_(None),
        )
        .execution_options(populate_existing=True)
    ).all()
    applicable: list[AccessGrant] = []
    for grant in grants:
        starts_ok = grant.starts_at is None or _as_utc(grant.starts_at) <= now
        expires_ok = grant.expires_at is None or _as_utc(grant.expires_at) > now
        project_ok = project_id is not None and grant.project_id == project_id
        location_ok = grant.location_id is None or (
            location_id is not None
            and is_descendant_or_self(session, location_id, grant.location_id)
        )
        if starts_ok and expires_ok and project_ok and location_ok:
            applicable.append(grant)

    if not applicable:
        raise AuthorizationError("No active access grant for the requested project and location")

    permissions = frozenset(permission for grant in applicable for permission in grant.permissions)
    return Principal(
        actor_id=actor.id,
        actor_organization_id=actor.organization_id,
        tenant_id=tenant_id,
        permissions=permissions,
        role="Scoped Contractor",
        is_tenant_member=False,
        grant_ids=tuple(grant.id for grant in applicable),
        project_id=project_id,
        location_id=location_id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )

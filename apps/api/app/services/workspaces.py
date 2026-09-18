"""Account workspace discovery and membership administration over tenant boundaries."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.config import Settings
from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.models import (
    AccessGrant,
    AccessGrantStatus,
    Location,
    Project,
    StandardProfile,
    Tenant,
    TenantMembership,
    UserIdentity,
)
from app.security import Principal, _as_utc, resolve_principal
from app.services.workspace_lock import lock_workspace

VIEWER_PERMISSIONS = [
    "location:read",
    "rack:read",
    "device:read",
    "port:read",
    "pathway:read",
    "cable:read",
    "cable:trace",
    "work_order:read",
    "dashboard:read",
    "compliance:read",
    "search:read",
    "audit:read",
    "report:export",
    "floor_plan:read",
    "fiber:read",
]
EDITOR_PERMISSIONS = VIEWER_PERMISSIONS + [
    "location:create",
    "location:update",
    "rack:create",
    "rack:update",
    "device:create",
    "device:update",
    "pathway:create",
    "pathway:update",
    "cable:create",
    "cable:install",
    "cable:test",
    "work_order:create",
    "label:create",
    "floor_plan:write",
    "fiber:write",
]


class WorkspaceService:
    def __init__(
        self,
        db: Session,
        actor: UserIdentity,
        claims: dict[str, Any] | None,
        settings: Settings,
        *,
        request_id: str | None = None,
    ):
        self.db = db
        self.actor = actor
        self.request_id = request_id
        if not actor.active:
            raise AuthorizationError("Unknown or inactive identity")
        bound = (claims or {}).get(settings.oidc_tenant_claim)
        try:
            self.bound_tenant = uuid.UUID(str(bound)) if bound is not None else None
        except (ValueError, TypeError, AttributeError) as exc:
            raise AuthorizationError("OIDC tenant claim is invalid") from exc

    @property
    def can_create(self) -> bool:
        return self.bound_tenant is None

    def _tenant(self, workspace_id: uuid.UUID, *, lock: bool = False) -> Tenant:
        if self.bound_tenant and self.bound_tenant != workspace_id:
            raise AuthorizationError("OIDC tenant claim does not match the requested workspace")
        tenant = (
            lock_workspace(self.db, workspace_id)
            if lock
            else self.db.scalar(
                select(Tenant).where(Tenant.id == workspace_id, Tenant.active.is_(True))
            )
        )
        if tenant is None or (
            tenant.workspace_kind == "personal" and tenant.workspace_owner_id != self.actor.id
        ):
            raise NotFoundError("Workspace not found")
        return tenant

    def _membership(self, workspace_id: uuid.UUID) -> TenantMembership | None:
        return self.db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == workspace_id,
                TenantMembership.user_id == self.actor.id,
                TenantMembership.active.is_(True),
                TenantMembership.deleted_at.is_(None),
            )
        )

    def _manager(self, workspace_id: uuid.UUID, *, lock: bool = True) -> tuple[Tenant, Principal]:
        tenant = self._tenant(workspace_id, lock=lock)
        principal = resolve_principal(
            self.db, actor_id=self.actor.id, tenant_id=workspace_id, request_id=self.request_id
        )
        if not principal.is_tenant_member or not principal.can("*"):
            raise AuthorizationError("Workspace management requires an owner or administrator")
        return tenant, principal

    def _grants(self, workspace_id: uuid.UUID) -> list[AccessGrant]:
        now = datetime.now(UTC)
        grants = self.db.scalars(
            select(AccessGrant)
            .join(
                Project,
                Project.id == AccessGrant.project_id,
            )
            .where(
                AccessGrant.tenant_id == workspace_id,
                AccessGrant.subject_user_id == self.actor.id,
                AccessGrant.status == AccessGrantStatus.ACTIVE,
                AccessGrant.deleted_at.is_(None),
                Project.tenant_id == workspace_id,
                Project.deleted_at.is_(None),
            )
            .order_by(AccessGrant.created_at, AccessGrant.id)
        ).all()
        return [
            grant
            for grant in grants
            if (
                (grant.starts_at is None or _as_utc(grant.starts_at) <= now)
                and (grant.expires_at is None or _as_utc(grant.expires_at) > now)
                and (
                    grant.location_id is None
                    or self.db.scalar(
                        select(Location.id).where(
                            Location.id == grant.location_id,
                            Location.tenant_id == workspace_id,
                            Location.deleted_at.is_(None),
                        )
                    )
                    is not None
                )
            )
        ]

    def _access_revision(
        self, tenant: Tenant, membership: TenantMembership | None, grants: list[AccessGrant]
    ) -> str:
        # Cover every effective scope without exposing another scope's permissions.
        # Canonical ordering avoids discarding drafts for equivalent query/list orderings.
        access = {
            "actor": [str(self.actor.id), str(self.actor.organization_id)],
            "workspace": [str(tenant.id), tenant.workspace_kind, str(tenant.workspace_owner_id)],
            "membership": [str(membership.id), membership.role, sorted(set(membership.permissions))]
            if membership
            else None,
            "grants": [
                [
                    str(grant.id),
                    str(grant.project_id),
                    str(grant.location_id),
                    sorted(set(grant.permissions)),
                    _as_utc(grant.starts_at).isoformat() if grant.starts_at else None,
                    _as_utc(grant.expires_at).isoformat() if grant.expires_at else None,
                ]
                for grant in sorted(grants, key=lambda item: str(item.id))
            ],
        }
        canonical = json.dumps(access, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def describe(self, tenant: Tenant) -> dict[str, Any]:
        membership = self._membership(tenant.id)
        grants: list[AccessGrant] = []
        scopes: list[dict[str, Any]] = []
        if membership:
            role = membership.role
            permissions = membership.permissions
        else:
            grants = self._grants(tenant.id)
            if not grants:
                raise NotFoundError("Workspace not found")
            role = "Scoped Contractor"
            # Preserve each grant boundary. Never combine different scope permissions.
            unique_scopes = dict.fromkeys((grant.project_id, grant.location_id) for grant in grants)
            scopes = [
                {"project_id": project, "location_id": location}
                for project, location in unique_scopes
            ]
            first = scopes[0]
            principal = resolve_principal(
                self.db, actor_id=self.actor.id, tenant_id=tenant.id, **first
            )
            permissions = sorted(principal.permissions)
        return {
            "id": tenant.id,
            "name": tenant.name,
            "kind": tenant.workspace_kind,
            "role": role,
            "can_manage": bool(membership and "*" in permissions),
            "is_owner": tenant.workspace_owner_id == self.actor.id,
            "can_leave": tenant.workspace_kind == "shared"
            and tenant.workspace_owner_id != self.actor.id
            and not (membership and "*" in permissions),
            "member_count": self.db.scalar(
                select(func.count())
                .select_from(TenantMembership)
                .where(
                    TenantMembership.tenant_id == tenant.id,
                    TenantMembership.active.is_(True),
                    TenantMembership.deleted_at.is_(None),
                )
            )
            if membership
            else None,
            "permissions": permissions,
            "access_revision": self._access_revision(tenant, membership, grants),
            "scopes": scopes,
            "project_id": scopes[0]["project_id"] if scopes else None,
            "location_id": scopes[0]["location_id"] if scopes else None,
        }

    def list_workspaces(self) -> list[dict[str, Any]]:
        membership_ids = select(TenantMembership.tenant_id).where(
            TenantMembership.user_id == self.actor.id,
            TenantMembership.active.is_(True),
            TenantMembership.deleted_at.is_(None),
        )
        grant_ids = select(AccessGrant.tenant_id).where(
            AccessGrant.subject_user_id == self.actor.id
        )
        query = (
            select(Tenant)
            .where(
                Tenant.active.is_(True),
                Tenant.id.in_(membership_ids.union(grant_ids)),
            )
            .order_by(Tenant.name, Tenant.id)
        )
        if self.bound_tenant:
            query = query.where(Tenant.id == self.bound_tenant)
        items = []
        for tenant in self.db.scalars(query):
            if tenant.workspace_kind == "personal" and tenant.workspace_owner_id != self.actor.id:
                continue
            try:
                items.append(self.describe(tenant))
            except NotFoundError:
                continue
        return items

    def _audit(
        self, principal: Principal, action: str, object_type: str, object_id: uuid.UUID, **values
    ):
        record_audit(
            self.db,
            principal=principal,
            action=action,
            object_type=object_type,
            object_id=object_id,
            **values,
        )

    def create(self, name: str, kind: str) -> dict[str, Any]:
        if not self.can_create:
            raise AuthorizationError("A tenant-bound credential cannot create another workspace")
        profile = self.db.scalar(
            select(StandardProfile)
            .where(
                StandardProfile.tenant_id.is_(None),
                StandardProfile.status == "active",
            )
            .order_by(StandardProfile.created_at, StandardProfile.id)
        )
        tenant = Tenant(
            name=name,
            slug=f"workspace-{uuid.uuid4().hex}",
            workspace_kind=kind,
            workspace_owner_id=self.actor.id,
            owner_organization_id=self.actor.organization_id,
            active_standard_profile_id=profile.id if profile else None,
        )
        self.db.add(tenant)
        self.db.flush()
        self.db.add(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=self.actor.id,
                role="owner",
                permissions=["*"],
                active=True,
            )
        )
        self.db.flush()
        principal = resolve_principal(
            self.db, actor_id=self.actor.id, tenant_id=tenant.id, request_id=self.request_id
        )
        self._audit(
            principal,
            "workspace.create",
            "workspace",
            tenant.id,
            after={"name": name, "kind": kind, "owner_id": str(self.actor.id)},
        )
        return self.describe(tenant)

    def update(
        self, workspace_id: uuid.UUID, *, name: str | None, kind: str | None
    ) -> dict[str, Any]:
        tenant, principal = self._manager(workspace_id)
        before = {"name": tenant.name, "kind": tenant.workspace_kind}
        if kind and kind != tenant.workspace_kind:
            if tenant.workspace_owner_id != self.actor.id:
                raise AuthorizationError("Only the workspace owner can change its sharing mode")
            if kind == "personal":
                others = self.db.scalar(
                    select(TenantMembership.id).where(
                        TenantMembership.tenant_id == tenant.id,
                        TenantMembership.user_id != self.actor.id,
                        TenantMembership.active.is_(True),
                        TenantMembership.deleted_at.is_(None),
                    )
                )
                grants = self.db.scalar(
                    select(AccessGrant.id).where(
                        AccessGrant.tenant_id == tenant.id,
                        AccessGrant.status.in_(
                            [AccessGrantStatus.ACTIVE, AccessGrantStatus.PENDING]
                        ),
                        AccessGrant.deleted_at.is_(None),
                    )
                )
                if others or grants:
                    raise ConflictError(
                        "Remove other members and revoke access grants before making this workspace personal"
                    )
            tenant.workspace_kind = kind
        if name is not None:
            tenant.name = name
        self.db.flush()
        after = {"name": tenant.name, "kind": tenant.workspace_kind}
        if before != after:
            self._audit(
                principal, "workspace.update", "workspace", tenant.id, before=before, after=after
            )
        return self.describe(tenant)

    def members(self, workspace_id: uuid.UUID) -> list[dict[str, Any]]:
        tenant, _ = self._manager(workspace_id, lock=False)
        scoped_users = set(
            self.db.scalars(
                select(AccessGrant.subject_user_id).where(
                    AccessGrant.tenant_id == tenant.id,
                    AccessGrant.status.in_([AccessGrantStatus.ACTIVE, AccessGrantStatus.PENDING]),
                    AccessGrant.deleted_at.is_(None),
                )
            )
        )
        rows = self.db.execute(
            select(TenantMembership, UserIdentity)
            .join(
                UserIdentity,
                UserIdentity.id == TenantMembership.user_id,
            )
            .where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.deleted_at.is_(None),
                TenantMembership.active.is_(True),
            )
            .order_by(UserIdentity.display_name, UserIdentity.id)
        )
        return [
            {
                "user_id": user.id,
                "email": user.email,
                "display_name": user.display_name,
                "role": member.role,
                "active": user.active,
                "is_owner": user.id == tenant.workspace_owner_id or "*" in member.permissions,
                "version": member.version,
                "has_scoped_access": user.id in scoped_users,
            }
            for member, user in rows
        ]

    def put_member(
        self, workspace_id: uuid.UUID, *, email: str, role: str, expected_version: int | None = None
    ) -> None:
        if role not in {"viewer", "editor"}:
            raise ValidationError("Workspace role must be viewer or editor")
        tenant, principal = self._manager(workspace_id)
        if tenant.workspace_kind != "shared":
            raise ConflictError("Convert this personal workspace to shared before adding members")
        user = self.db.scalar(
            select(UserIdentity).where(
                func.lower(UserIdentity.email) == email.strip().lower(),
                UserIdentity.active.is_(True),
            )
        )
        if user is None:
            raise NotFoundError("An active provisioned account with this email was not found")
        membership = self.db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == user.id,
            )
        )
        if expected_version is not None and (
            membership is None or membership.version != expected_version
        ):
            raise ConflictError(
                "Member permissions changed; refresh the member list before retrying"
            )
        if user.id in (tenant.workspace_owner_id, self.actor.id) or (
            membership and "*" in membership.permissions
        ):
            raise ConflictError(
                "The owner, administrator, or your own membership cannot be changed here"
            )
        grant = self.db.scalar(
            select(AccessGrant.id).where(
                AccessGrant.tenant_id == tenant.id,
                AccessGrant.subject_user_id == user.id,
                AccessGrant.status.in_([AccessGrantStatus.ACTIVE, AccessGrantStatus.PENDING]),
                AccessGrant.deleted_at.is_(None),
            )
        )
        if grant and not (membership and membership.active and membership.deleted_at is None):
            raise ConflictError(
                "This account has scoped access; revoke those grants before granting workspace-wide membership"
            )
        before = {"role": membership.role, "active": membership.active} if membership else None
        if membership is None:
            membership = TenantMembership(tenant_id=tenant.id, user_id=user.id)
            self.db.add(membership)
        membership.role = role
        membership.permissions = list(
            VIEWER_PERMISSIONS if role == "viewer" else EDITOR_PERMISSIONS
        )
        membership.active = True
        membership.deleted_at = None
        self.db.flush()
        self._audit(
            principal,
            "workspace.member.upsert",
            "tenant_membership",
            membership.id,
            before=before,
            after={"user_id": str(user.id), "role": role, "active": True},
        )

    def remove_member(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        expected_version: int | None = None,
        revoke_scoped_access: bool = False,
    ) -> None:
        tenant, principal = self._manager(workspace_id)
        membership = self.db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == user_id,
                TenantMembership.active.is_(True),
                TenantMembership.deleted_at.is_(None),
            )
        )
        if membership is None:
            raise NotFoundError("Workspace member not found")
        if expected_version is not None and membership.version != expected_version:
            raise ConflictError(
                "Member permissions changed; refresh the member list before retrying"
            )
        if user_id in (tenant.workspace_owner_id, self.actor.id) or "*" in membership.permissions:
            raise ConflictError(
                "The owner, administrator, or your own membership cannot be removed here"
            )
        membership.active = False
        if revoke_scoped_access:
            self._revoke_user_grants(tenant.id, user_id, principal)
        self._audit(
            principal,
            "workspace.member.remove",
            "tenant_membership",
            membership.id,
            before={"user_id": str(user_id), "role": membership.role, "active": True},
            after={"active": False},
        )

    def _revoke_user_grants(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID, principal: Principal
    ) -> None:
        grants = self.db.scalars(
            select(AccessGrant).where(
                AccessGrant.tenant_id == workspace_id,
                AccessGrant.subject_user_id == user_id,
                AccessGrant.status.in_([AccessGrantStatus.ACTIVE, AccessGrantStatus.PENDING]),
                AccessGrant.deleted_at.is_(None),
            )
        ).all()
        for grant in grants:
            before = grant.status.value
            grant.status = AccessGrantStatus.REVOKED
            grant.revoked_at = datetime.now(UTC)
            grant.revoked_by = self.actor.id
            self._audit(
                principal,
                "accessgrant.revoked",
                "access_grant",
                grant.id,
                before={"status": before},
                after={"status": "revoked", "reason": "workspace_access_removed"},
                project_id=grant.project_id,
            )

    def leave(self, workspace_id: uuid.UUID) -> None:
        tenant = self._tenant(workspace_id, lock=True)
        descriptor = self.describe(tenant)
        if not descriptor["can_leave"]:
            raise ConflictError("The owner or administrator cannot leave this workspace")
        principal = resolve_principal(
            self.db,
            actor_id=self.actor.id,
            tenant_id=tenant.id,
            project_id=descriptor["project_id"],
            location_id=descriptor["location_id"],
            request_id=self.request_id,
        )
        membership = self._membership(tenant.id)
        if membership:
            membership.active = False
        self._revoke_user_grants(tenant.id, self.actor.id, principal)
        self._audit(
            principal,
            "workspace.leave",
            "workspace",
            tenant.id,
            after={"user_id": str(self.actor.id), "membership_active": False},
        )

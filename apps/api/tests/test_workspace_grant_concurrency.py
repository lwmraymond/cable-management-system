"""Sharing-mode changes and legacy grant routes use one transaction boundary."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.exceptions import AuthorizationError, ConflictError
from app.main import create_access_grant
from app.models import (
    AccessGrant,
    AccessGrantStatus,
    Base,
    Tenant,
    TenantMembership,
    UserIdentity,
)
from app.schemas import AccessGrantCreate
from app.security import resolve_principal
from app.services.account_scope_endpoints import AccountScopeEndpoints
from app.services.workspaces import WorkspaceService


@pytest.fixture
def session_factory(tmp_path):
    # Separate real connections are necessary; StaticPool would share a transaction.
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'workspace-lock.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def owner_only_world(world):
    with world.session_factory() as db:
        tenant = db.get(Tenant, world.tenant_a)
        tenant.workspace_owner_id = world.admin
        for member in db.scalars(
            select(TenantMembership).where(TenantMembership.tenant_id == world.tenant_a)
        ):
            if member.user_id != world.admin:
                member.active = False
        for grant in db.scalars(select(AccessGrant).where(AccessGrant.tenant_id == world.tenant_a)):
            grant.status = AccessGrantStatus.REVOKED
        db.commit()
    return world


def workspace_service(db, world):
    return WorkspaceService(
        db,
        db.get(UserIdentity, world.admin),
        None,
        Settings(_env_file=None, auth_mode="demo", demo_mode=True),
    )


def grant_payload(world):
    return AccessGrantCreate(
        project_id=world.project,
        subject_user_id=world.contractor,
        subject_organization_id=world.contractor_org,
        permissions=["rack:read"],
    )


def test_grant_creation_blocks_personal_conversion_until_new_grant_is_visible(owner_only_world):
    world = owner_only_world
    attempted = Event()

    def convert_to_personal():
        with world.session_factory() as db:

            def before_execute(_connection, _cursor, statement, _parameters, _context, _many):
                if statement.startswith("UPDATE tenants"):
                    attempted.set()

            event.listen(db.connection(), "before_cursor_execute", before_execute)
            workspace_service(db, world).update(world.tenant_a, name=None, kind="personal")
            db.commit()

    with world.session_factory() as db, ThreadPoolExecutor(max_workers=1) as executor:
        principal = resolve_principal(db, actor_id=world.admin, tenant_id=world.tenant_a)
        payload = grant_payload(world)
        AccountScopeEndpoints(db, principal).validate_grant(payload)
        future = executor.submit(convert_to_personal)
        try:
            assert attempted.wait(2), "Conversion did not reach the competing transaction"
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
            created = create_access_grant(payload, db=db, principal=principal)
            assert created["status"] == "active"
            with pytest.raises(ConflictError, match="revoke access grants"):
                future.result(timeout=3)
        finally:
            db.rollback()

    with world.session_factory() as db:
        assert db.get(Tenant, world.tenant_a).workspace_kind == "shared"
        assert (
            len(
                db.scalars(
                    select(AccessGrant).where(
                        AccessGrant.tenant_id == world.tenant_a,
                        AccessGrant.status == AccessGrantStatus.ACTIVE,
                    )
                ).all()
            )
            == 1
        )


def test_grant_request_rechecks_sharing_state_after_personal_conversion(owner_only_world):
    world = owner_only_world
    with world.session_factory() as waiting, world.session_factory() as owner:
        cached_tenant = waiting.get(Tenant, world.tenant_a)
        principal = resolve_principal(waiting, actor_id=world.admin, tenant_id=world.tenant_a)
        pending = AccountScopeEndpoints(waiting, principal)
        workspace_service(owner, world).update(world.tenant_a, name=None, kind="personal")
        owner.commit()
        assert cached_tenant.workspace_kind == "shared"
        with pytest.raises(AuthorizationError, match="Personal workspaces cannot grant"):
            pending.validate_grant(grant_payload(world))


@pytest.mark.parametrize("operation", ["create", "revoke"])
def test_grant_request_rechecks_membership_after_removal(world, operation):
    with world.session_factory() as setup:
        member = setup.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == world.tenant_a,
                TenantMembership.user_id == world.supervisor,
            )
        )
        member.permissions = ["access_grant:create", "access_grant:revoke"]
        grant_id = setup.scalar(
            select(AccessGrant.id).where(
                AccessGrant.tenant_id == world.tenant_a,
                AccessGrant.status == AccessGrantStatus.ACTIVE,
            )
        )
        setup.commit()
    with world.session_factory() as waiting, world.session_factory() as owner:
        principal = resolve_principal(waiting, actor_id=world.supervisor, tenant_id=world.tenant_a)
        pending = AccountScopeEndpoints(waiting, principal)
        workspace_service(owner, world).remove_member(world.tenant_a, world.supervisor)
        owner.commit()
        with pytest.raises(AuthorizationError):
            if operation == "create":
                pending.validate_grant(grant_payload(world))
            else:
                pending.revocable_grant(grant_id)

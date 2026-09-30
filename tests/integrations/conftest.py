from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app import db as tenant_events
from app.integration_models import INTEGRATION_TABLES  # noqa: F401
from app.models import (
    Base,
    Device,
    Location,
    LocationType,
    Organization,
    OrganizationType,
    Project,
    Tenant,
    TenantMembership,
    UserIdentity,
)
from app.security import Principal, resolve_principal
from app.services.netbox_adapter import NetBoxSyncService
from app.services.signed_webhooks import SignedWebhookService


@pytest.fixture
def integration_env(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'integrations.db'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enforce_fks(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)
    db = factory()
    org = Organization(name="Integration Customer", organization_type=OrganizationType.CUSTOMER)
    other_org = Organization(name="Other Customer", organization_type=OrganizationType.CUSTOMER)
    db.add_all([org, other_org]); db.flush()
    owner = UserIdentity(organization_id=org.id, email="owner@integration.invalid", display_name="Owner")
    reader = UserIdentity(organization_id=org.id, email="reader@integration.invalid", display_name="Reader")
    other = UserIdentity(organization_id=other_org.id, email="other@integration.invalid", display_name="Other")
    db.add_all([owner, reader, other]); db.flush()
    tenant = Tenant(owner_organization_id=org.id, name="Integration", slug="integration")
    other_tenant = Tenant(owner_organization_id=other_org.id, name="Other", slug="other-integration")
    db.add_all([tenant, other_tenant]); db.flush()
    db.add_all([
        TenantMembership(tenant_id=tenant.id, user_id=owner.id, role="Owner", permissions=["*"]),
        TenantMembership(
            tenant_id=tenant.id,
            user_id=reader.id,
            role="Reader",
            permissions=["integration:read", "webhook:read"],
        ),
        TenantMembership(tenant_id=other_tenant.id, user_id=other.id, role="Owner", permissions=["*"]),
    ])
    project = Project(
        tenant_id=tenant.id,
        project_number="INT-001",
        name="Integration Project",
        customer_organization_id=org.id,
    )
    location = Location(
        tenant_id=tenant.id,
        identifier="INT-LOC",
        name="Integration Location",
        location_type=LocationType.SITE,
    )
    db.add_all([project, location]); db.flush()
    device = Device(
        tenant_id=tenant.id,
        location_id=location.id,
        identifier="NB-DEVICE-1",
        name="Mapped device",
        device_type="switch",
    )
    db.add(device); db.commit()
    tenant_events.set_postgres_tenant_context(db, tenant.id)
    principal = resolve_principal(db, actor_id=owner.id, tenant_id=tenant.id)

    def actor_principal(actor):
        return Principal(
            actor_id=actor.id,
            actor_organization_id=actor.organization_id,
            tenant_id=tenant.id,
            permissions=frozenset({"*"}),
            role="Untrusted cached role",
            is_tenant_member=True,
        )

    def write(callable_, *args, **kwargs):
        try:
            result = callable_(*args, **kwargs)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise

    yield SimpleNamespace(
        db=db,
        engine=engine,
        factory=factory,
        tenant=tenant,
        other_tenant=other_tenant,
        owner=owner,
        reader=reader,
        project=project,
        location=location,
        device=device,
        principal=principal,
        netbox=lambda actor=None: NetBoxSyncService(db, principal if actor is None else actor_principal(actor)),
        webhooks=lambda actor=None: SignedWebhookService(db, principal if actor is None else actor_principal(actor)),
        write=write,
    )
    db.close(); engine.dispose()

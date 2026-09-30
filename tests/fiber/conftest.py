from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app import db as tenant_events  # Register the real application ORM guards.
from app.models import (
    AccessGrant, AccessGrantStatus, Base, Cable, Device, Location, LocationType,
    Organization, OrganizationType, Port, Project, Tenant, TenantMembership, UserIdentity,
)
from app.fiber_models import FIBER_TABLES  # noqa: F401
from app.security import Principal, resolve_principal
from app.services.fiber import FiberService


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'fiber.db'}",
                           connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def enforce_fks(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)
    session = factory()
    tenants, owners, projects, locations, devices, cables = [], [], [], [], [], []
    fiber_ports, copper_ports, copper_cables = [], [], []
    for n in range(2):
        org = Organization(name=f"customer-{n}", organization_type=OrganizationType.CUSTOMER)
        session.add(org); session.flush()
        owner = UserIdentity(organization_id=org.id, email=f"owner{n}@example.invalid",
                             display_name=f"Owner {n}")
        session.add(owner); session.flush()
        tenant = Tenant(owner_organization_id=org.id, name=f"tenant-{n}", slug=f"t-{n}")
        session.add(tenant); session.flush()
        session.add(TenantMembership(tenant_id=tenant.id, user_id=owner.id,
                                     role="Owner", permissions=["*"]))
        tenants.append(tenant); owners.append(owner)
        for k in range(2):
            project = Project(tenant_id=tenant.id, project_number=f"P-{k}", name=f"Project {k}",
                              customer_organization_id=org.id)
            loc = Location(tenant_id=tenant.id, identifier=f"TR-{k}", name=f"Room {k}",
                           location_type=LocationType.TR)
            session.add_all([project, loc]); session.flush()
            device = Device(tenant_id=tenant.id, location_id=loc.id, identifier=f"D-{n}-{k}",
                            name=f"ODF {n}-{k}", device_type="patch_panel")
            session.add(device); session.flush()
            projects.append(project); locations.append(loc); devices.append(device)
            for i in range(1, 5):
                fiber_port = Port(
                    tenant_id=tenant.id,
                    device_id=device.id,
                    identifier=f"F{i}",
                    label=f"Fiber {i}",
                    connector_type="LC",
                    media_type="fiber_os2",
                    position_index=i,
                )
                copper_port = Port(
                    tenant_id=tenant.id,
                    device_id=device.id,
                    identifier=f"C{i}",
                    label=f"Copper {i}",
                    connector_type="RJ45",
                    media_type="copper_cat6a",
                    position_index=100 + i,
                )
                session.add_all([fiber_port, copper_port])
                fiber_ports.append(fiber_port)
                copper_ports.append(copper_port)
            for i in range(4):
                cable = Cable(tenant_id=tenant.id, project_id=project.id,
                              identifier=f"F-{n}-{k}-{i}", media_type="fiber_os2",
                              construction="trunk", strand_count=4)
                session.add(cable); cables.append(cable)
            copper_cable = Cable(
                tenant_id=tenant.id,
                project_id=project.id,
                identifier=f"CU-{n}-{k}",
                media_type="copper_cat6a",
                construction="horizontal",
                pair_count=4,
            )
            session.add(copper_cable)
            copper_cables.append(copper_cable)
    contractor_org = Organization(name="contractor", organization_type=OrganizationType.CONTRACTOR)
    session.add(contractor_org); session.flush()
    contractor = UserIdentity(organization_id=contractor_org.id,
                               email="tech@example.invalid", display_name="Technician")
    reader = UserIdentity(organization_id=owners[0].organization_id,
                          email="reader@example.invalid", display_name="Reader")
    session.add_all([contractor, reader]); session.flush()
    session.add(TenantMembership(tenant_id=tenants[0].id, user_id=reader.id,
                                 role="Reader", permissions=["fiber:read"]))
    grant = AccessGrant(tenant_id=tenants[0].id, subject_organization_id=contractor_org.id,
                         subject_user_id=contractor.id, project_id=projects[0].id,
                         location_id=locations[0].id, approved_by=owners[0].id,
                         permissions=["fiber:read", "fiber:write"],
                         starts_at=datetime.now(UTC)-timedelta(days=1),
                         expires_at=datetime.now(UTC)+timedelta(days=1),
                         status=AccessGrantStatus.ACTIVE)
    session.add(grant); session.commit()
    tenant_events.set_postgres_tenant_context(session, tenants[0].id)
    owner_principal = resolve_principal(session, actor_id=owners[0].id, tenant_id=tenants[0].id)

    def service(actor=None, db=None):
        db = db or session
        if actor is None:
            p = owner_principal
        else:
            p = Principal(actor_id=actor.id, actor_organization_id=actor.organization_id,
                          tenant_id=tenants[0].id, permissions=frozenset({"*"}),
                          role="Untrusted cached role", is_tenant_member=True,
                          project_id=projects[0].id, location_id=locations[0].id)
        return FiberService(db, p)

    def write(method, *args, **kwargs):
        try:
            result = method(*args, **kwargs)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise

    yield SimpleNamespace(db=session, engine=engine, factory=factory, tenants=tenants,
                           owners=owners, projects=projects, locations=locations,
                           devices=devices, cables=cables, fiber_ports=fiber_ports,
                           copper_ports=copper_ports, copper_cables=copper_cables,
                           contractor=contractor, reader=reader, grant=grant,
                           principal=owner_principal, service=service, write=write)
    session.close(); engine.dispose()

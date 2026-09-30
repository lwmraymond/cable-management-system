from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app import db as tenant_events  # register local ORM criteria
from app.floorplan_models import FLOOR_PLAN_TABLES  # noqa: F401
from app.models import (
    AccessGrant,
    AccessGrantStatus,
    Base,
    Device,
    Location,
    LocationType,
    Organization,
    OrganizationType,
    Pathway,
    Project,
    Rack,
    Tenant,
    TenantMembership,
    UserIdentity,
)
from app.security import Principal, resolve_principal
from app.services.floorplan import FloorPlanService


@pytest.fixture
def env(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'floorplan.db'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
        autoflush=False,
    )
    session = factory()
    tenants = []
    owners = []
    projects = []
    floors = []
    rooms = []
    racks = []
    devices = []
    pathways = []
    for index in range(2):
        organization = Organization(
            name=f"Customer {index}",
            organization_type=OrganizationType.CUSTOMER,
        )
        session.add(organization)
        session.flush()
        owner = UserIdentity(
            organization_id=organization.id,
            email=f"owner{index}@example.invalid",
            display_name=f"Owner {index}",
        )
        session.add(owner)
        session.flush()
        tenant = Tenant(
            owner_organization_id=organization.id,
            name=f"Tenant {index}",
            slug=f"tenant-{index}",
        )
        session.add(tenant)
        session.flush()
        session.add(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=owner.id,
                role="Owner",
                permissions=["*"],
            )
        )
        project = Project(
            tenant_id=tenant.id,
            project_number=f"P-{index}",
            name=f"Project {index}",
            customer_organization_id=organization.id,
        )
        floor = Location(
            tenant_id=tenant.id,
            identifier=f"F-{index}",
            name=f"Floor {index}",
            location_type=LocationType.FLOOR,
        )
        session.add_all([project, floor])
        session.flush()
        room = Location(
            tenant_id=tenant.id,
            parent_id=floor.id,
            identifier=f"TR-{index}",
            name=f"Room {index}",
            location_type=LocationType.TR,
        )
        session.add(room)
        session.flush()
        rack = Rack(
            tenant_id=tenant.id,
            location_id=room.id,
            rack_identifier=f"R-{index}",
            name=f"Rack {index}",
        )
        device = Device(
            tenant_id=tenant.id,
            location_id=room.id,
            identifier=f"D-{index}",
            name=f"Device {index}",
            device_type="patch_panel",
        )
        pathway = Pathway(
            tenant_id=tenant.id,
            location_id=room.id,
            identifier=f"PW-{index}",
            name=f"Pathway {index}",
            pathway_type="tray",
        )
        session.add_all([rack, device, pathway])
        tenants.append(tenant)
        owners.append(owner)
        projects.append(project)
        floors.append(floor)
        rooms.append(room)
        racks.append(rack)
        devices.append(device)
        pathways.append(pathway)

    reader = UserIdentity(
        organization_id=owners[0].organization_id,
        email="reader@example.invalid",
        display_name="Reader",
    )
    session.add(reader)
    session.flush()
    session.add(
        TenantMembership(
            tenant_id=tenants[0].id,
            user_id=reader.id,
            role="Reader",
            permissions=["floor_plan:read"],
        )
    )
    contractor_org = Organization(
        name="Contractor",
        organization_type=OrganizationType.CONTRACTOR,
    )
    session.add(contractor_org)
    session.flush()
    contractor = UserIdentity(
        organization_id=contractor_org.id,
        email="contractor@example.invalid",
        display_name="Contractor",
    )
    session.add(contractor)
    session.flush()
    grant = AccessGrant(
        tenant_id=tenants[0].id,
        subject_organization_id=contractor_org.id,
        subject_user_id=contractor.id,
        project_id=projects[0].id,
        location_id=floors[0].id,
        permissions=["floor_plan:read", "floor_plan:write"],
        approved_by=owners[0].id,
        starts_at=datetime.now(UTC) - timedelta(days=1),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        status=AccessGrantStatus.ACTIVE,
    )
    session.add(grant)
    session.commit()
    tenant_events.set_postgres_tenant_context(session, tenants[0].id)
    owner_principal = resolve_principal(
        session,
        actor_id=owners[0].id,
        tenant_id=tenants[0].id,
    )

    def principal_for(actor):
        if actor is None:
            return owner_principal
        return Principal(
            actor_id=actor.id,
            actor_organization_id=actor.organization_id,
            tenant_id=tenants[0].id,
            permissions=frozenset({"*"}),
            role="Untrusted cached role",
            is_tenant_member=True,
            project_id=projects[0].id,
            location_id=floors[0].id,
        )

    def service(actor=None, db=None):
        return FloorPlanService(db or session, principal_for(actor))

    def write_call(method, *args, **kwargs):
        try:
            result = method(*args, **kwargs)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise

    yield SimpleNamespace(
        db=session,
        engine=engine,
        factory=factory,
        tenants=tenants,
        owners=owners,
        projects=projects,
        floors=floors,
        rooms=rooms,
        racks=racks,
        devices=devices,
        pathways=pathways,
        reader=reader,
        contractor=contractor,
        grant=grant,
        principal=owner_principal,
        service=service,
        write=write_call,
    )
    session.close()
    engine.dispose()

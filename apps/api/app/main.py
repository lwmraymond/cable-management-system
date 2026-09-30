from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_platform_db, get_principal
from app.api.fiber import build_fiber_router
from app.api.fiber_advanced import build_fiber_advanced_router
from app.audit import record_audit
from app.config import get_settings
from app.db import SessionLocal
from app.exceptions import DomainError, NotFoundError
from app.http_security import SecurityBoundaryMiddleware
from app.models import (
    AccessGrant,
    AccessGrantStatus,
    AuditEvent,
    Cable,
    CableStatus,
    Device,
    DeviceTemplate,
    Location,
    Organization,
    OrganizationType,
    Pathway,
    Port,
    Project,
    Rack,
    StandardProfile,
    Tenant,
    TenantMembership,
    TestRecord,
    UserIdentity,
    WorkOrder,
    WorkOrderStatus,
)
from app.schemas import (
    AccessGrantCreate,
    CableCreate,
    CableRead,
    DeviceCreate,
    DeviceRead,
    DeviceTemplateCreate,
    DeviceTemplateRead,
    LocationCreate,
    LocationRead,
    PathwayCreate,
    PortRead,
    RackCreate,
    RackRead,
    TestRecordRead,
    TestSubmit,
    WorkOrderCreate,
    WorkOrderRead,
)
from app.security import Principal, require_permission
from app.services.compliance import ComplianceService
from app.services.connectivity import ConnectivityService
from app.services.infrastructure import InfrastructureService
from app.services.labels import LabelService
from app.services.reporting import ReportingService
from app.services.topology_trace import TopologyTraceService
from app.services.workflow import CableWorkflowService

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="API-first physical infrastructure source of truth",
    openapi_url=f"{settings.api_prefix}/openapi.json",
    docs_url=f"{settings.api_prefix}/docs",
)
app.include_router(build_fiber_router(get_db, get_principal), prefix=settings.api_prefix)
app.include_router(build_fiber_advanced_router(get_db, get_principal), prefix=settings.api_prefix)
app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=list(settings.cors_methods),
    allow_headers=list(settings.cors_headers),
    expose_headers=list(settings.cors_expose_headers),
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))


@app.exception_handler(DomainError)
async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc)},
        headers=headers,
    )


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_request: Request, _exc: IntegrityError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "Database constraint rejected the change"})


def _create_global_profile(db: Session) -> StandardProfile:
    profile = StandardProfile(
        name="TIA-606-D Configurable Baseline",
        standard_family="TIA-606",
        edition="D",
        effective_date=datetime(2021, 1, 1, tzinfo=UTC),
        status="active",
        rules_json={
            "identifier_regex": r"^[A-Z0-9][A-Z0-9-]{2,179}$",
            "compliance_modes": ["strict", "assisted", "custom"],
            "notice": "Policy scaffolding only; exact proprietary clauses require a licensed source.",
        },
        identifier_templates={
            "location": "{parent}-{code}",
            "rack": "{campus}-{building}-{floor}-{space}-R{sequence:02d}",
            "device": "{campus}-{building}-{floor}-{space}-{kind}{sequence:02d}",
            "cable": "{campus}-{building}-{floor}-{space}-{kind}-{sequence:05d}",
        },
        validation_rules=[
            {"id": "generic-identifier-format", "type": "regex", "severity": "warning", "clause": None}
        ],
        label_templates={
            "cable-default": {
                "human_readable": "{identifier}",
                "barcode": "qr",
                "dimensions_mm": [50, 25],
            }
        },
        required_records={
            "cable": ["identifier", "media_type", "termination_a", "termination_b"],
            "rack": ["identifier", "location", "height_u"],
        },
    )
    db.add(profile)
    db.flush()
    return profile


@app.get(f"{settings.api_prefix}/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get(f"{settings.api_prefix}/ready", tags=["system"])
def ready() -> dict[str, str]:
    with SessionLocal() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.post(f"{settings.api_prefix}/platform/bootstrap", tags=["platform"])
def bootstrap_tenant(
    body: dict[str, str],
    x_platform_key: Annotated[str, Header(alias="X-Platform-Key")],
    db: Session = Depends(get_platform_db),
) -> Any:
    if x_platform_key != settings.platform_bootstrap_key:
        return JSONResponse(status_code=403, content={"detail": "Invalid platform bootstrap key"})
    required = {"tenant_name", "tenant_slug", "owner_email", "owner_name"}
    missing = required - body.keys()
    if missing:
        return JSONResponse(status_code=422, content={"detail": f"Missing fields: {sorted(missing)}"})
    profile = db.scalar(
        select(StandardProfile).where(
            StandardProfile.tenant_id.is_(None),
            StandardProfile.standard_family == "TIA-606",
            StandardProfile.edition == "D",
        )
    ) or _create_global_profile(db)
    organization = Organization(
        name=body["tenant_name"], organization_type=OrganizationType.CUSTOMER
    )
    db.add(organization)
    db.flush()
    owner = UserIdentity(
        organization_id=organization.id,
        email=body["owner_email"].lower(),
        display_name=body["owner_name"],
    )
    db.add(owner)
    db.flush()
    tenant = Tenant(
        owner_organization_id=organization.id,
        name=body["tenant_name"],
        slug=body["tenant_slug"],
        active_standard_profile_id=profile.id,
    )
    db.add(tenant)
    db.flush()
    db.add(
        TenantMembership(
            tenant_id=tenant.id,
            user_id=owner.id,
            role="Tenant Owner",
            permissions=["*"],
        )
    )
    db.commit()
    return {"tenant_id": str(tenant.id), "owner_id": str(owner.id)}


@app.get(f"{settings.api_prefix}/tenants/current", tags=["tenancy"])
def current_tenant(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    tenant = db.get(Tenant, principal.tenant_id)
    if not tenant:
        raise NotFoundError("Tenant not found")
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "slug": tenant.slug,
        "compliance_mode": tenant.compliance_mode,
        "principal": {
            "actor_id": str(principal.actor_id),
            "role": principal.role,
            "is_tenant_member": principal.is_tenant_member,
            "permissions": sorted(principal.permissions),
        },
    }


@app.get(f"{settings.api_prefix}/dashboard", tags=["reporting"])
def dashboard(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    return ReportingService(db, principal).dashboard()


@app.get(f"{settings.api_prefix}/reports/cable-schedule.csv", tags=["reporting"])
def export_cable_schedule(
    status: CableStatus | None = None,
    project_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=180)] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 10_000,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    export = ReportingService(db, principal).export_cable_schedule(
        status=status,
        project_id=project_id,
        query=q,
        limit=limit,
    )
    record_audit(
        db,
        principal=principal,
        action="report.cable_schedule.exported",
        object_type="cable_schedule",
        object_id=principal.tenant_id,
        after={
            "format": "csv",
            "filename": export.filename,
            "row_count": export.row_count,
            "total_count": export.total_count,
            "truncated": export.truncated,
            "filters": export.filters,
        },
        project_id=project_id,
    )
    db.commit()
    return Response(
        content=export.content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename}"',
            "Cache-Control": "no-store",
            "X-Export-Row-Count": str(export.row_count),
            "X-Export-Total-Count": str(export.total_count),
            "X-Export-Truncated": str(export.truncated).lower(),
        },
    )


@app.get(f"{settings.api_prefix}/search", tags=["search"])
def global_search(
    q: Annotated[str, Query(min_length=1, max_length=180)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    items = ReportingService(db, principal).search(q, limit)
    return {"items": items, "total": len(items)}


@app.get(f"{settings.api_prefix}/locations", response_model=list[LocationRead], tags=["infrastructure"])
def list_locations(
    parent_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    require_permission(principal, "location:read")
    statement = select(Location).order_by(Location.identifier)
    if parent_id is not None:
        statement = statement.where(Location.parent_id == parent_id)
    return db.scalars(statement).all()


@app.post(
    f"{settings.api_prefix}/locations",
    response_model=LocationRead,
    status_code=201,
    tags=["infrastructure"],
)
def create_location(
    payload: LocationCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    location = InfrastructureService(db, principal).create_location(**payload.model_dump())
    db.commit()
    return location


@app.get(f"{settings.api_prefix}/racks", response_model=list[RackRead], tags=["infrastructure"])
def list_racks(
    location_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    require_permission(principal, "rack:read")
    statement = select(Rack).order_by(Rack.rack_identifier)
    if location_id:
        statement = statement.where(Rack.location_id == location_id)
    return db.scalars(statement).all()


@app.post(
    f"{settings.api_prefix}/racks",
    response_model=RackRead,
    status_code=201,
    tags=["infrastructure"],
)
def create_rack(
    payload: RackCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    rack = InfrastructureService(db, principal).create_rack(**payload.model_dump())
    db.commit()
    return rack


@app.get(f"{settings.api_prefix}/racks/{{rack_id}}/elevation", tags=["infrastructure"])
def rack_elevation(
    rack_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return InfrastructureService(db, principal).rack_elevation(rack_id)


@app.get(
    f"{settings.api_prefix}/device-templates",
    response_model=list[DeviceTemplateRead],
    tags=["assets"],
)
def list_device_templates(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
):
    require_permission(principal, "device:read")
    return db.scalars(
        select(DeviceTemplate).order_by(DeviceTemplate.manufacturer, DeviceTemplate.model)
    ).all()


@app.post(
    f"{settings.api_prefix}/device-templates",
    response_model=DeviceTemplateRead,
    status_code=201,
    tags=["assets"],
)
def create_device_template(
    payload: DeviceTemplateCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    template = InfrastructureService(db, principal).create_template(**payload.model_dump())
    db.commit()
    return template


@app.get(f"{settings.api_prefix}/devices", response_model=list[DeviceRead], tags=["assets"])
def list_devices(
    rack_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    require_permission(principal, "device:read")
    statement = select(Device).order_by(Device.identifier)
    if rack_id:
        statement = statement.where(Device.rack_id == rack_id)
    return db.scalars(statement).all()


@app.post(
    f"{settings.api_prefix}/devices",
    response_model=DeviceRead,
    status_code=201,
    tags=["assets"],
)
def create_device(
    payload: DeviceCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    device = InfrastructureService(db, principal).create_device_from_template(**payload.model_dump())
    db.commit()
    return device


@app.get(f"{settings.api_prefix}/ports", response_model=list[PortRead], tags=["connectivity"])
def list_ports(
    device_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    require_permission(principal, "port:read")
    return db.scalars(
        select(Port)
        .where(Port.device_id == device_id)
        .order_by(Port.front_or_rear, Port.position_index)
    ).all()


@app.get(f"{settings.api_prefix}/pathways", tags=["pathways"])
def list_pathways(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> list[dict[str, Any]]:
    require_permission(principal, "pathway:read")
    rows = db.scalars(select(Pathway).order_by(Pathway.identifier)).all()
    return [
        {
            "id": str(row.id),
            "identifier": row.identifier,
            "name": row.name,
            "type": row.pathway_type,
            "capacity_area_mm2": row.capacity_area_mm2,
        }
        for row in rows
    ]


@app.post(f"{settings.api_prefix}/pathways", status_code=201, tags=["pathways"])
def create_pathway(
    payload: PathwayCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    data = payload.model_dump()
    segments = data.pop("segments")
    pathway = InfrastructureService(db, principal).create_pathway(segments=segments, **data)
    db.commit()
    return {"id": str(pathway.id), "identifier": pathway.identifier}


@app.get(f"{settings.api_prefix}/cables", response_model=list[CableRead], tags=["connectivity"])
def list_cables(
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    require_permission(principal, "cable:read")
    statement = select(Cable).order_by(Cable.identifier).limit(limit).offset(offset)
    if status:
        statement = statement.where(Cable.installation_status == status)
    return db.scalars(statement).all()


@app.post(
    f"{settings.api_prefix}/cables",
    response_model=CableRead,
    status_code=201,
    tags=["connectivity"],
)
def create_cable(
    payload: CableCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    cable = ConnectivityService(db, principal).create_cable(**payload.model_dump())
    db.commit()
    return cable


@app.get(f"{settings.api_prefix}/cables/{{cable_id}}/trace", tags=["connectivity"])
def trace_cable(
    cable_id: uuid.UUID,
    strand_number: Annotated[int | None, Query(ge=1, le=576)] = None,
    pair_number: Annotated[int | None, Query(ge=1, le=600)] = None,
    max_nodes: Annotated[int, Query(ge=2, le=1000)] = 500,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return TopologyTraceService(db, principal).trace_cable(
        cable_id,
        strand_number=strand_number,
        pair_number=pair_number,
        max_nodes=max_nodes,
    )


@app.get(
    f"{settings.api_prefix}/work-orders",
    response_model=list[WorkOrderRead],
    tags=["operations"],
)
def list_work_orders(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
):
    require_permission(principal, "work_order:read")
    return db.scalars(select(WorkOrder).order_by(WorkOrder.created_at.desc())).all()


@app.post(
    f"{settings.api_prefix}/work-orders",
    response_model=WorkOrderRead,
    status_code=201,
    tags=["operations"],
)
def create_work_order(
    payload: WorkOrderCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    require_permission(principal, "work_order:create")
    work_order = WorkOrder(
        tenant_id=principal.tenant_id,
        status=WorkOrderStatus.READY,
        created_by=principal.actor_id,
        **payload.model_dump(),
    )
    db.add(work_order)
    db.flush()
    record_audit(
        db,
        principal=principal,
        action="workorder.created",
        object_type="work_order",
        object_id=work_order.id,
        after={"number": work_order.work_order_number},
        project_id=work_order.project_id,
    )
    db.commit()
    return work_order


@app.post(
    f"{settings.api_prefix}/cables/{{cable_id}}/install",
    response_model=CableRead,
    tags=["operations"],
)
def install_cable(
    cable_id: uuid.UUID,
    work_order_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    cable = CableWorkflowService(db, principal).mark_installed(cable_id, work_order_id)
    db.commit()
    return cable


@app.post(
    f"{settings.api_prefix}/cables/{{cable_id}}/tests",
    response_model=TestRecordRead,
    status_code=201,
    tags=["operations"],
)
def submit_test(
    cable_id: uuid.UUID,
    payload: TestSubmit,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    record = CableWorkflowService(db, principal).submit_test(
        cable_id=cable_id, **payload.model_dump()
    )
    db.commit()
    return record


@app.get(
    f"{settings.api_prefix}/test-results",
    response_model=list[TestRecordRead],
    tags=["operations"],
)
def list_test_results(
    cable_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    require_permission(principal, "cable:read")
    statement = select(TestRecord).order_by(TestRecord.tested_at.desc())
    if cable_id:
        statement = statement.where(TestRecord.cable_id == cable_id)
    return db.scalars(statement).all()


@app.post(
    f"{settings.api_prefix}/tests/{{test_id}}/approve",
    response_model=TestRecordRead,
    tags=["operations"],
)
def approve_test(
    test_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    record = CableWorkflowService(db, principal).approve_test(test_id)
    db.commit()
    return record


@app.post(f"{settings.api_prefix}/access-grants", status_code=201, tags=["tenancy"])
def create_access_grant(
    payload: AccessGrantCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    require_permission(principal, "access_grant:create")
    grant = AccessGrant(
        tenant_id=principal.tenant_id,
        approved_by=principal.actor_id,
        status=AccessGrantStatus.ACTIVE,
        **payload.model_dump(),
    )
    db.add(grant)
    db.flush()
    record_audit(
        db,
        principal=principal,
        action="accessgrant.created",
        object_type="access_grant",
        object_id=grant.id,
        after={
            "subject_user_id": str(grant.subject_user_id) if grant.subject_user_id else None,
            "project_id": str(grant.project_id),
            "location_id": str(grant.location_id) if grant.location_id else None,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        },
        project_id=grant.project_id,
    )
    db.commit()
    return {"id": str(grant.id), "status": grant.status.value}


@app.post(f"{settings.api_prefix}/access-grants/{{grant_id}}/revoke", tags=["tenancy"])
def revoke_access_grant(
    grant_id: uuid.UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    require_permission(principal, "access_grant:revoke")
    grant = db.get(AccessGrant, grant_id)
    if not grant:
        raise NotFoundError("Access grant not found")
    grant.status = AccessGrantStatus.REVOKED
    grant.revoked_at = datetime.now(UTC)
    grant.revoked_by = principal.actor_id
    record_audit(
        db,
        principal=principal,
        action="accessgrant.revoked",
        object_type="access_grant",
        object_id=grant.id,
        after={"status": grant.status.value},
        project_id=grant.project_id,
    )
    db.commit()
    return {"id": str(grant.id), "status": grant.status.value}


@app.post(
    f"{settings.api_prefix}/labels/cables/{{cable_id}}",
    status_code=201,
    tags=["compliance"],
)
def create_cable_label(
    cable_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    label, svg = LabelService(db, principal).create_cable_label(
        cable_id, str(request.base_url).rstrip("/")
    )
    db.commit()
    return {
        "id": str(label.id),
        "identifier": label.identifier,
        "qr_payload": label.qr_payload,
        "qr_svg": svg,
    }


@app.get(f"{settings.api_prefix}/compliance/report", tags=["compliance"])
def compliance_report(
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
) -> dict[str, Any]:
    return ComplianceService(db, principal).report()


@app.get(f"{settings.api_prefix}/audit-events", tags=["audit"])
def audit_events(
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    require_permission(principal, "audit:read")
    statement = select(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(limit)
    if object_type:
        statement = statement.where(AuditEvent.object_type == object_type)
    if object_id:
        statement = statement.where(AuditEvent.object_id == object_id)
    rows = db.scalars(statement).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "timestamp": row.timestamp.isoformat(),
                "actor_id": str(row.actor_id),
                "action": row.action,
                "object_type": row.object_type,
                "object_id": str(row.object_id),
                "before": row.before,
                "after": row.after,
                "request_id": row.request_id,
            }
            for row in rows
        ],
        "total": len(rows),
    }


@app.get(f"{settings.api_prefix}/demo/context", tags=["demo"])
def demo_context(db: Session = Depends(get_platform_db)) -> dict[str, str]:
    if not settings.demo_mode:
        raise NotFoundError("Demo mode is disabled")
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "northstar-university"))
    if not tenant:
        raise NotFoundError("Run python -m app.seed first")
    owner = db.scalar(
        select(UserIdentity).where(UserIdentity.email == "alice.admin@northstar.example")
    )
    supervisor = db.scalar(
        select(UserIdentity).where(UserIdentity.email == "sam.supervisor@northstar.example")
    )
    contractor = db.scalar(
        select(UserIdentity).where(UserIdentity.email == "tina.tech@metro.example")
    )
    rack = db.scalar(
        select(Rack)
        .execution_options(skip_tenant_criteria=True)
        .where(
            Rack.tenant_id == tenant.id,
            Rack.rack_identifier == "MC-ENG-F02-TR02-R01",
        )
    )
    cable = db.scalar(
        select(Cable)
        .execution_options(skip_tenant_criteria=True)
        .where(
            Cable.tenant_id == tenant.id,
            Cable.identifier == "MC-ENG-F02-TR02-HC-00001",
        )
    )
    work_order = db.scalar(
        select(WorkOrder)
        .execution_options(skip_tenant_criteria=True)
        .where(
            WorkOrder.tenant_id == tenant.id,
            WorkOrder.work_order_number == "WO-ENG-0001",
        )
    )
    project = db.scalar(
        select(Project)
        .execution_options(skip_tenant_criteria=True)
        .where(
            Project.tenant_id == tenant.id,
            Project.project_number == "ENG-UPG-2026",
        )
    )
    telecom_room = db.scalar(
        select(Location)
        .execution_options(skip_tenant_criteria=True)
        .where(
            Location.tenant_id == tenant.id,
            Location.identifier == "MC-ENG-F02-TR02",
        )
    )
    return {
        "tenant_id": str(tenant.id),
        "owner_id": str(owner.id) if owner else "",
        "supervisor_id": str(supervisor.id) if supervisor else "",
        "contractor_id": str(contractor.id) if contractor else "",
        "rack_id": str(rack.id) if rack else "",
        "cable_id": str(cable.id) if cable else "",
        "work_order_id": str(work_order.id) if work_order else "",
        "project_id": str(project.id) if project else "",
        "location_id": str(telecom_room.id) if telecom_room else "",
        "api_prefix": settings.api_prefix,
    }


APPS_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = APPS_ROOT / "web"
WEB_REACT_DIST = APPS_ROOT / "web-react" / "dist"
if WEB_ROOT.exists():
    app.mount("/app", StaticFiles(directory=WEB_ROOT, html=True), name="web")
if WEB_REACT_DIST.exists():
    app.mount(
        "/app-next",
        StaticFiles(directory=WEB_REACT_DIST, html=True),
        name="web-react",
    )


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/app/")

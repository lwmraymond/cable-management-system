from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import deps
from app.api.projects import build_projects_router
from app.exceptions import DomainError
from app.models import AccessGrant, AuditEvent, Project, Tenant, TenantMembership


@pytest.fixture
def project_client(world, monkeypatch):
    monkeypatch.setattr(deps, "SessionLocal", world.session_factory)
    app = FastAPI()
    app.include_router(build_projects_router(deps.get_db, deps.get_principal), prefix="/api/v1")

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, error: DomainError):
        return JSONResponse(status_code=error.status_code, content={"detail": str(error)})

    return TestClient(app)


def headers(world, actor=None):
    return {"X-Tenant-ID": str(world.tenant_a), "X-Actor-ID": str(actor or world.admin)}


def test_project_creation_uses_workspace_owner_and_commits_audit_atomically(world, project_client):
    response = project_client.post(
        "/api/v1/projects",
        headers=headers(world),
        json={"project_number": " HPC-NET-01 ", "name": " 超算网络 "},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["project_number"] == "HPC-NET-01"
    assert data["name"] == "超算网络"
    assert data["status"] == "active"
    with world.scoped_session() as db:
        project = db.get(Project, uuid.UUID(data["id"]))
        assert project.tenant_id == world.tenant_a
        assert (
            project.customer_organization_id == db.get(Tenant, world.tenant_a).owner_organization_id
        )
        event = db.scalar(select(AuditEvent).where(AuditEvent.object_id == project.id))
        assert event.action == "project.created"
        assert event.actor_id == world.admin
        assert event.project_id == project.id
    catalog = project_client.get("/api/v1/projects", headers=headers(world)).json()
    assert catalog["can_create"] is True
    assert data["id"] in {row["id"] for row in catalog["projects"]}


def test_duplicate_and_tenant_injection_do_not_create_project_or_audit(world, project_client):
    body = {"project_number": "HPC-NET-01", "name": "HPC"}
    assert (
        project_client.post("/api/v1/projects", headers=headers(world), json=body).status_code
        == 201
    )
    assert (
        project_client.post("/api/v1/projects", headers=headers(world), json=body).status_code
        == 409
    )
    assert (
        project_client.post(
            "/api/v1/projects",
            headers=headers(world),
            json={**body, "tenant_id": str(world.tenant_b)},
        ).status_code
        == 422
    )
    with world.scoped_session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "project.created")
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(Project)
                .where(Project.project_number == "HPC-NET-01")
            )
            == 1
        )


def test_project_write_forbidden_for_viewer_and_contractor_even_with_cable_create(
    world, project_client
):
    body = {"project_number": "HPC-NET-01", "name": "HPC"}
    viewer = headers(world, world.supervisor)
    assert project_client.get("/api/v1/projects", headers=viewer).json()["can_create"] is False
    assert project_client.post("/api/v1/projects", headers=viewer, json=body).status_code == 403
    with world.scoped_session() as db:
        grant = db.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        grant.permissions = [*grant.permissions, "cable:create"]
        source = db.get(Project, world.project)
        db.add(
            Project(
                tenant_id=world.tenant_a,
                customer_organization_id=source.customer_organization_id,
                project_number="HIDDEN-PROJECT",
                name="Hidden",
            )
        )
        db.commit()
    contractor = {
        **headers(world, world.contractor),
        "X-Project-ID": str(world.project),
        "X-Location-ID": str(world.tr),
    }
    catalog = project_client.get("/api/v1/projects", headers=contractor)
    assert catalog.status_code == 200, catalog.text
    assert [row["id"] for row in catalog.json()["projects"]] == [str(world.project)]
    assert catalog.json()["can_create"] is False
    assert project_client.post("/api/v1/projects", headers=contractor, json=body).status_code == 403
    assert (
        project_client.get("/api/v1/projects", headers=headers(world, world.contractor)).status_code
        == 403
    )


def test_cross_workspace_and_revoked_membership_are_denied(world, project_client):
    body = {"project_number": "HPC-NET-01", "name": "HPC"}
    other = {**headers(world), "X-Tenant-ID": str(world.tenant_b)}
    assert project_client.get("/api/v1/projects", headers=other).status_code == 403
    assert project_client.post("/api/v1/projects", headers=other, json=body).status_code == 403
    with world.scoped_session() as db:
        membership = db.scalar(
            select(TenantMembership).where(TenantMembership.user_id == world.admin)
        )
        membership.active = False
        db.commit()
    assert project_client.get("/api/v1/projects", headers=headers(world)).status_code == 403
    assert (
        project_client.post("/api/v1/projects", headers=headers(world), json=body).status_code
        == 403
    )


def test_failed_audit_rolls_back_project(world, project_client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.api.projects.record_audit", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        project_client.post(
            "/api/v1/projects",
            headers=headers(world),
            json={"project_number": "HPC-ROLLBACK", "name": "HPC"},
        )
    with world.scoped_session() as db:
        assert db.scalar(select(Project).where(Project.project_number == "HPC-ROLLBACK")) is None

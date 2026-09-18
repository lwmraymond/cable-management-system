"""Workspace project catalog and audited creation for the cabling workflow."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import AuthorizationError, ConflictError, NotFoundError
from app.models import Project
from app.schemas import APIModel
from app.security import Principal, require_permission
from app.services.resource_scope import ResourceScope
from app.services.workspace_lock import lock_workspace


class ProjectCreate(APIModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_number: str = Field(min_length=3, max_length=80)
    name: str = Field(min_length=1, max_length=180)


class ProjectRead(APIModel):
    id: uuid.UUID
    project_number: str
    name: str
    status: str
    version: int


def build_projects_router(get_db: Callable, get_principal: Callable) -> APIRouter:
    router = APIRouter(prefix="/projects", tags=["projects"])

    @router.get("")
    def projects(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
        principal = ResourceScope(db, principal).principal
        require_permission(principal, "cable:read")
        query = select(Project).where(
            Project.tenant_id == principal.tenant_id, Project.deleted_at.is_(None)
        )
        if principal.project_id:
            query = query.where(Project.id == principal.project_id)
        # Contractors can only resolve their selected, currently authorized project.
        if not principal.is_tenant_member and not principal.project_id:
            raise AuthorizationError("A scoped project is required")
        rows = db.scalars(query.order_by(Project.project_number, Project.id).limit(1001)).all()
        return {
            "projects": [ProjectRead.model_validate(row) for row in rows[:1000]],
            "truncated": len(rows) > 1000,
            "can_create": principal.is_tenant_member and principal.can("cable:create"),
        }

    @router.post("", response_model=ProjectRead, status_code=201)
    def create_project(
        body: ProjectCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        try:
            tenant = lock_workspace(db, principal.tenant_id)
            if tenant is None:
                raise NotFoundError("Workspace not found")
            principal = ResourceScope(db, principal).principal
            require_permission(principal, "cable:create")
            if not principal.is_tenant_member:
                raise AuthorizationError("Project creation requires workspace membership")
            project = Project(
                tenant_id=principal.tenant_id,
                customer_organization_id=tenant.owner_organization_id,
                **body.model_dump(),
            )
            db.add(project)
            db.flush()
            record_audit(
                db,
                principal=principal,
                action="project.created",
                object_type="project",
                object_id=project.id,
                project_id=project.id,
                after=ProjectRead.model_validate(project).model_dump(mode="json"),
            )
            db.commit()
            return project
        except IntegrityError:
            db.rollback()
            raise ConflictError("Project number already exists in this workspace") from None
        except OperationalError:
            db.rollback()
            raise HTTPException(
                503, "Database is busy; reload before retrying", headers={"Retry-After": "1"}
            ) from None
        except Exception:
            db.rollback()
            raise

    return router

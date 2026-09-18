"""Account-scoped workspace APIs; no caller-selected tenant is trusted for discovery."""

from __future__ import annotations

import secrets
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.orm import Session

from app.api import deps
from app.services.workspaces import WorkspaceService

WorkspaceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)
]


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: WorkspaceName
    kind: Literal["personal", "shared"] = "personal"


class WorkspaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: WorkspaceName | None = None
    kind: Literal["personal", "shared"] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if self.name is None and self.kind is None:
            raise ValueError("Provide a workspace name or sharing mode")
        return self


class WorkspaceMemberPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    role: Literal["viewer", "editor"]
    expected_version: int | None = Field(default=None, ge=1)


def get_workspace_service(
    request: Request,
    identity: deps.AuthenticatedActor = Depends(deps.get_authenticated_actor),
    db: Session = Depends(deps.get_platform_db),
) -> WorkspaceService:
    return WorkspaceService(
        db,
        identity.actor,
        identity.claims,
        deps.get_settings(),
        request_id=getattr(request.state, "request_id", None),
    )


def build_workspaces_router() -> APIRouter:
    router = APIRouter(tags=["workspaces"])

    @router.get("/auth/session")
    def session_status(
        request: Request,
        response: Response,
        identity: deps.AuthenticatedActor = Depends(deps.get_authenticated_actor),
        service: WorkspaceService = Depends(get_workspace_service),
    ):
        settings = deps.get_settings()
        response.headers["Cache-Control"] = "no-store"
        if identity.auth_method == "cookie" and not request.cookies.get(settings.csrf_cookie_name):
            response.set_cookie(
                settings.csrf_cookie_name,
                secrets.token_urlsafe(32),
                path="/",
                secure=settings.cookie_secure,
                httponly=False,
                samesite=settings.cookie_samesite,
            )
        return {
            "user": {
                "id": identity.actor.id,
                "display_name": identity.actor.display_name,
                "email": identity.actor.email,
            },
            "auth_method": identity.auth_method,
            "workspaces": service.list_workspaces(),
            "can_create_workspaces": service.can_create,
            "csrf_header_name": settings.csrf_header_name,
            "csrf_cookie_name": settings.csrf_cookie_name,
        }

    @router.get("/workspaces")
    def list_workspaces(
        response: Response, service: WorkspaceService = Depends(get_workspace_service)
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.list_workspaces()

    @router.post("/workspaces", status_code=201)
    def create_workspace(
        body: WorkspaceCreate, service: WorkspaceService = Depends(get_workspace_service)
    ):
        result = service.create(body.name, body.kind)
        service.db.commit()
        return result

    @router.patch("/workspaces/{workspace_id}")
    def update_workspace(
        workspace_id: uuid.UUID,
        body: WorkspaceUpdate,
        service: WorkspaceService = Depends(get_workspace_service),
    ):
        result = service.update(workspace_id, name=body.name, kind=body.kind)
        service.db.commit()
        return result

    @router.get("/workspaces/{workspace_id}/members")
    def list_members(
        workspace_id: uuid.UUID,
        response: Response,
        service: WorkspaceService = Depends(get_workspace_service),
    ):
        response.headers["Cache-Control"] = "no-store"
        return service.members(workspace_id)

    @router.put("/workspaces/{workspace_id}/members")
    def put_member(
        workspace_id: uuid.UUID,
        body: WorkspaceMemberPut,
        service: WorkspaceService = Depends(get_workspace_service),
    ):
        service.put_member(
            workspace_id, email=body.email, role=body.role, expected_version=body.expected_version
        )
        result = service.members(workspace_id)
        service.db.commit()
        return result

    @router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
    def remove_member(
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        expected_version: Annotated[int | None, Query(ge=1)] = None,
        revoke_scoped_access: bool = False,
        service: WorkspaceService = Depends(get_workspace_service),
    ):
        service.remove_member(
            workspace_id,
            user_id,
            expected_version=expected_version,
            revoke_scoped_access=revoke_scoped_access,
        )
        service.db.commit()
        return Response(status_code=204)

    @router.post("/workspaces/{workspace_id}/leave", status_code=204)
    def leave_workspace(
        workspace_id: uuid.UUID, service: WorkspaceService = Depends(get_workspace_service)
    ):
        service.leave(workspace_id)
        service.db.commit()
        return Response(status_code=204)

    return router

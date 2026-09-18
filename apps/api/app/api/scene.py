"""Read-only spatial inventory for the 3D workspace."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.scene_editor import build_scene_editor_router
from app.security import Principal
from app.services.scene import SceneService


def build_scene_router(get_db: Callable, get_principal: Callable) -> APIRouter:
    router = APIRouter(prefix="/scene", tags=["scene"])

    @router.get("")
    def read_scene(
        location_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        """Return scoped source geometry; missing positions are never invented.

        Query parameters may narrow, but cannot widen the project/location
        context selected through authentication headers. Infrastructure is
        location-scoped; cable records are additionally project-scoped.
        """
        return SceneService(db, principal).read(location_id=location_id, project_id=project_id)

    @router.get("/ports")
    def read_ports(
        location_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        after: uuid.UUID | None = None,
        limit: int = Query(default=10000, ge=1, le=10000),
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return SceneService(db, principal).read_ports(
            location_id=location_id, project_id=project_id, after=after, limit=limit
        )

    router.include_router(build_scene_editor_router(get_db, get_principal))
    return router

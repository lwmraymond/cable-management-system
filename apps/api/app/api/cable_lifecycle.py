"""Thin cable retirement routes; all writes commit or roll back together."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.scene_editor import commit_scene_edit
from app.schemas_cable_lifecycle import CableDeletionPreview, CableLifecycleResult, CableRemoval
from app.security import Principal
from app.services.cable_lifecycle import CableLifecycleService


def build_cable_lifecycle_router(get_db: Callable, get_principal: Callable) -> APIRouter:
    router = APIRouter(prefix="/cables", tags=["connectivity"])

    @router.get("/{cable_id}/deletion-preview", response_model=CableDeletionPreview)
    def preview(
        cable_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return CableLifecycleService(db, principal).preview(cable_id)

    @router.delete("/{cable_id}", response_model=CableLifecycleResult)
    def delete(
        cable_id: uuid.UUID,
        expected_version: int = Query(ge=1),
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_scene_edit(
            db,
            lambda: CableLifecycleService(db, principal).retire(
                cable_id,
                action="delete",
                expected_version=expected_version,
            ),
        )

    @router.post("/{cable_id}/remove", response_model=CableLifecycleResult)
    def remove(
        cable_id: uuid.UUID,
        body: CableRemoval,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_scene_edit(
            db,
            lambda: CableLifecycleService(db, principal).retire(
                cable_id,
                action="remove",
                expected_version=body.expected_version,
                reason=body.reason,
            ),
        )

    return router

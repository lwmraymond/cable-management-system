"""Strict API for the versioned 2D floor-plan editor."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.exceptions import ConflictError
from app.security import Principal
from app.services.floorplan_editor import FloorPlanService


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FloorPlanCreate(StrictPayload):
    project_id: uuid.UUID
    location_id: uuid.UUID
    name: str = Field(min_length=1, max_length=180)
    width_mm: float = Field(gt=0, le=1_000_000, allow_inf_nan=False, strict=True)
    height_mm: float = Field(gt=0, le=1_000_000, allow_inf_nan=False, strict=True)
    grid_mm: float = Field(default=100.0, gt=0, le=100_000, allow_inf_nan=False, strict=True)
    background_object_key: str | None = Field(default=None, min_length=1, max_length=500)


class RevisionCreate(StrictPayload):
    expected_version: int = Field(ge=1, strict=True)
    document: dict[str, Any]
    note: str = Field(default="", max_length=500)


class PublishRequest(StrictPayload):
    expected_version: int = Field(ge=1, strict=True)
    revision: int = Field(ge=1, strict=True)


class RestoreRequest(StrictPayload):
    expected_version: int = Field(ge=1, strict=True)
    source_revision: int = Field(ge=1, strict=True)
    note: str = Field(default="", max_length=500)


def commit_operation(db: Session, operation: Callable, **kwargs):
    try:
        result = operation(**kwargs)
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        raise ConflictError("Floor plan, revision or scoped name already exists") from None
    except OperationalError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Database is temporarily unavailable; reload before retrying",
            headers={"Retry-After": "1"},
        ) from None
    except Exception:
        db.rollback()
        raise


def build_floorplan_router(get_db: Callable, get_principal: Callable) -> APIRouter:
    router = APIRouter(prefix="/floor-plans", tags=["floor-plan"])

    @router.post("", status_code=201)
    def create_plan(
        body: FloorPlanCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FloorPlanService(db, principal).create_plan,
            **body.model_dump(),
        )

    @router.get("")
    def list_plans(
        project_id: uuid.UUID,
        location_id: uuid.UUID,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return FloorPlanService(db, principal).list_plans(project_id, location_id, limit)

    @router.get("/{plan_id}")
    def get_plan(
        plan_id: uuid.UUID,
        view: Literal["working", "published"] = "working",
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return FloorPlanService(db, principal).get_plan(plan_id, view)

    @router.post("/{plan_id}/revisions", status_code=201)
    def save_revision(
        plan_id: uuid.UUID,
        body: RevisionCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FloorPlanService(db, principal).save_revision,
            plan_id=plan_id,
            **body.model_dump(),
        )

    @router.get("/{plan_id}/revisions")
    def list_revisions(
        plan_id: uuid.UUID,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return FloorPlanService(db, principal).list_revisions(plan_id, limit)

    @router.post("/{plan_id}/publish")
    def publish(
        plan_id: uuid.UUID,
        body: PublishRequest,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FloorPlanService(db, principal).publish,
            plan_id=plan_id,
            **body.model_dump(),
        )

    @router.post("/{plan_id}/restore", status_code=201)
    def restore(
        plan_id: uuid.UUID,
        body: RestoreRequest,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FloorPlanService(db, principal).restore,
            plan_id=plan_id,
            **body.model_dump(),
        )

    return router

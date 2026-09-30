"""Versioned 2D Floor Plan API using host authentication dependencies."""
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
from app.services.floorplan import FloorPlanService


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlanCreate(StrictPayload):
    project_id: uuid.UUID
    location_id: uuid.UUID
    name: str = Field(min_length=1, max_length=180)
    units: Literal["mm", "m", "ft"] = "mm"
    canvas_width: float = Field(gt=0, le=1_000_000, allow_inf_nan=False, strict=True)
    canvas_height: float = Field(gt=0, le=1_000_000, allow_inf_nan=False, strict=True)
    background_reference: str | None = Field(default=None, min_length=1, max_length=500)
    document: dict[str, Any] | None = None


class PlanVersion(StrictPayload):
    expected_version: int = Field(ge=1, strict=True)


class RevisionSave(PlanVersion):
    document: dict[str, Any]
    change_summary: str = Field(default="", max_length=500)


class RestoreRevision(PlanVersion):
    change_summary: str = Field(default="", max_length=500)


def commit_operation(db: Session, operation: Callable, **kwargs):
    try:
        result = operation(**kwargs)
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        raise ConflictError("Floor Plan name, revision or reference conflicts") from None
    except OperationalError:
        db.rollback()
        raise HTTPException(
            503,
            "Database is temporarily unavailable; reload before retrying",
            headers={"Retry-After": "1"},
        ) from None
    except Exception:
        db.rollback()
        raise


def build_floorplan_router(get_db: Callable, get_principal: Callable) -> APIRouter:
    router = APIRouter(prefix="/floor-plans", tags=["floor-plans"])

    @router.post("", status_code=201)
    def create_plan(
        body: PlanCreate,
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
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return FloorPlanService(db, principal).list_plans(
            project_id=project_id,
            location_id=location_id,
            limit=limit,
        )

    @router.get("/{plan_id}")
    def get_plan(
        plan_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return FloorPlanService(db, principal).get_plan(plan_id)

    @router.put("/{plan_id}/draft")
    def save_revision(
        plan_id: uuid.UUID,
        body: RevisionSave,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FloorPlanService(db, principal).save_revision,
            plan_id=plan_id,
            **body.model_dump(),
        )

    @router.post("/{plan_id}/publish")
    def publish(
        plan_id: uuid.UUID,
        body: PlanVersion,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FloorPlanService(db, principal).publish,
            plan_id=plan_id,
            **body.model_dump(),
        )

    @router.get("/{plan_id}/revisions")
    def revisions(
        plan_id: uuid.UUID,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return FloorPlanService(db, principal).revisions(plan_id, limit=limit)

    @router.post("/{plan_id}/revisions/{revision_id}/restore")
    def restore(
        plan_id: uuid.UUID,
        revision_id: uuid.UUID,
        body: RestoreRevision,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            FloorPlanService(db, principal).restore,
            plan_id=plan_id,
            revision_id=revision_id,
            **body.model_dump(),
        )

    return router

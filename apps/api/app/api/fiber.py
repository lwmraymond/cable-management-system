"""Fiber API, using the host application's existing authentication dependencies."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.api.floorplan import build_floorplan_router
from app.exceptions import ConflictError
from app.security import Principal
from app.services.fiber import FiberService


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BundleCreate(StrictPayload):
    cable_id: uuid.UUID
    name: str = Field(min_length=1, max_length=180)


class CassetteCreate(StrictPayload):
    device_id: uuid.UUID
    project_id: uuid.UUID
    name: str = Field(min_length=1, max_length=180)
    slot_count: int = Field(ge=1, le=288, strict=True)


class SlotVersion(StrictPayload):
    expected_version: int = Field(ge=1, strict=True)


class SpliceCreate(SlotVersion):
    left_strand_id: uuid.UUID
    left_side: Literal["A", "B"]
    right_strand_id: uuid.UUID
    right_side: Literal["A", "B"]
    loss_db: float = Field(default=0.0, ge=0, le=10, allow_inf_nan=False, strict=True)


def commit_operation(db: Session, operation: Callable, **kwargs):
    try:
        result = operation(**kwargs)
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        raise ConflictError("Resource, slot or endpoint already exists or is occupied") from None
    except OperationalError:
        db.rollback()
        raise HTTPException(503, "Database is temporarily unavailable; reload before retrying",
                            headers={"Retry-After": "1"}) from None
    except Exception:
        db.rollback()
        raise


def build_fiber_router(get_db: Callable, get_principal: Callable) -> APIRouter:
    root = APIRouter()
    router = APIRouter(prefix="/fiber", tags=["fiber"])

    @router.post("/bundles", status_code=201)
    def create_bundle(body: BundleCreate, db: Session = Depends(get_db),
                      principal: Principal = Depends(get_principal)):
        return commit_operation(db, FiberService(db, principal).provision_bundle,
                                **body.model_dump())

    @router.get("/bundles/{bundle_id}")
    def get_bundle(bundle_id: uuid.UUID, db: Session = Depends(get_db),
                   principal: Principal = Depends(get_principal)):
        return FiberService(db, principal).get_bundle(bundle_id)

    @router.get("/cables/{cable_id}/bundle")
    def find_bundle(cable_id: uuid.UUID, db: Session = Depends(get_db),
                    principal: Principal = Depends(get_principal)):
        return FiberService(db, principal).find_bundle(cable_id)

    @router.post("/cassettes", status_code=201)
    def create_cassette(body: CassetteCreate, db: Session = Depends(get_db),
                        principal: Principal = Depends(get_principal)):
        return commit_operation(db, FiberService(db, principal).create_cassette,
                                **body.model_dump())

    @router.get("/cassettes/{cassette_id}")
    def get_cassette(cassette_id: uuid.UUID, db: Session = Depends(get_db),
                     principal: Principal = Depends(get_principal)):
        return FiberService(db, principal).get_cassette(cassette_id)

    @router.get("/devices/{device_id}/cassettes")
    def list_cassettes(device_id: uuid.UUID, project_id: uuid.UUID,
                       limit: Annotated[int, Query(ge=1, le=100)] = 100,
                       db: Session = Depends(get_db),
                       principal: Principal = Depends(get_principal)):
        return FiberService(db, principal).list_cassettes(device_id, project_id, limit)

    @router.post("/slots/{slot_id}/splice", status_code=201)
    def splice(slot_id: uuid.UUID, body: SpliceCreate, db: Session = Depends(get_db),
               principal: Principal = Depends(get_principal)):
        return commit_operation(db, FiberService(db, principal).splice,
                                slot_id=slot_id, **body.model_dump())

    @router.post("/slots/{slot_id}/release")
    def release(slot_id: uuid.UUID, body: SlotVersion, db: Session = Depends(get_db),
                principal: Principal = Depends(get_principal)):
        return commit_operation(db, FiberService(db, principal).release,
                                slot_id=slot_id, **body.model_dump())

    @router.get("/strands/{strand_id}/trace")
    def trace(strand_id: uuid.UUID, entry_side: Literal["A", "B"] = "A",
              max_hops: Annotated[int, Query(ge=1, le=256)] = 64,
              db: Session = Depends(get_db),
              principal: Principal = Depends(get_principal)):
        return FiberService(db, principal).trace(strand_id, entry_side, max_hops)

    root.include_router(router)
    # Mount Floor Plan beside /fiber under the host API prefix without requiring
    # a large unrelated rewrite of app.main.
    root.include_router(build_floorplan_router(get_db, get_principal))
    return root

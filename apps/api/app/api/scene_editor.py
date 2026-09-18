"""Authenticated editing routes under the scene router."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.exceptions import ConflictError
from app.models import Device, Pathway
from app.schemas import (
    CableCreate,
    CableRead,
    DeviceCreate,
    DeviceRead,
    LocationRead,
    PathwayCreate,
    RackRead,
)
from app.schemas_scene_editor import (
    CablePolicyUpdate,
    RackGridCreate,
    RackPoseUpdate,
    RoomCreate,
    RoomEntrancesUpdate,
    RoutePreview,
)
from app.security import Principal
from app.services.scene_editor import SceneEditorService


class SceneEditorRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                # Never echo NaN/Infinity inputs into JSON error responses.
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {key: issue[key] for key in ("loc", "msg", "type")}
                            for issue in error.errors()
                        ]
                    },
                )

        return validated_handler


def commit_scene_edit(db: Session, operation: Callable):
    try:
        result = operation()
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            "Identifier, placement or port occupation conflicts with an existing record"
        ) from None
    except OperationalError:
        db.rollback()
        raise HTTPException(
            503, "Database is busy; reload before retrying", headers={"Retry-After": "1"}
        ) from None
    except Exception:
        db.rollback()
        raise


def build_scene_editor_router(get_db: Callable, get_principal: Callable) -> APIRouter:
    router = APIRouter(route_class=SceneEditorRoute)

    @router.post("/rooms", response_model=LocationRead, status_code=201)
    def create_room(
        body: RoomCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_scene_edit(db, lambda: SceneEditorService(db, principal).create_room(body))

    @router.post("/racks", status_code=201)
    def create_racks(
        body: RackGridCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        def operation():
            rows = SceneEditorService(db, principal).create_racks(body)
            return {"racks": [RackRead.model_validate(row).model_dump(mode="json") for row in rows]}

        return commit_scene_edit(db, operation)

    @router.patch("/racks/{rack_id}", response_model=RackRead)
    def move_rack(
        rack_id: uuid.UUID,
        body: RackPoseUpdate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_scene_edit(
            db, lambda: SceneEditorService(db, principal).move_rack(rack_id, body)
        )

    @router.post("/devices", response_model=DeviceRead, status_code=201)
    def create_device(
        body: DeviceCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_scene_edit(db, lambda: SceneEditorService(db, principal).create_device(body))

    @router.post("/pathways", status_code=201)
    def create_pathway(
        body: PathwayCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        def operation():
            row = SceneEditorService(db, principal).create_pathway(body)
            return {"id": str(row.id), "identifier": row.identifier}

        return commit_scene_edit(db, operation)

    @router.post("/cables", response_model=CableRead, status_code=201)
    def create_cable(
        body: CableCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_scene_edit(db, lambda: SceneEditorService(db, principal).create_cable(body))

    @router.patch("/pathways/{pathway_id}/cable-policy")
    def pathway_policy(
        pathway_id: uuid.UUID,
        body: CablePolicyUpdate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_scene_edit(
            db, lambda: SceneEditorService(db, principal).update_policy(Pathway, pathway_id, body)
        )

    @router.patch("/devices/{device_id}/cable-policy")
    def device_policy(
        device_id: uuid.UUID,
        body: CablePolicyUpdate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_scene_edit(
            db, lambda: SceneEditorService(db, principal).update_policy(Device, device_id, body)
        )

    @router.patch("/rooms/{room_id}/entrances")
    def room_entrances(
        room_id: uuid.UUID,
        body: RoomEntrancesUpdate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_scene_edit(
            db, lambda: SceneEditorService(db, principal).update_entrances(room_id, body)
        )

    @router.post("/routes/preview")
    def preview_routes(
        body: RoutePreview,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        from app.services.scene_routing import SceneRouteService

        return SceneRouteService(SceneEditorService(db, principal)).preview(body)

    return router

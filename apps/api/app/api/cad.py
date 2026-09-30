"""CAD originals never overwrite uploaded files; applying a revision is explicit."""

import uuid
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.scene_editor import commit_scene_edit
from app.cad_models import CadImportRevision, CadSnapshot
from app.exceptions import ValidationError
from app.security import Principal
from app.services.cad_formats import capabilities
from app.services.cad_geometry import MAX_FILE_BYTES
from app.services.cad_sync import CadSyncService


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: uuid.UUID
    format: Literal["dxf", "ifc"]


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")


def build_cad_router(get_db: Callable, get_principal: Callable):
    router = APIRouter(prefix="/cad")

    @router.get("/capabilities")
    def available(principal: Principal = Depends(get_principal)):
        return capabilities()

    @router.post("/exports", status_code=201)
    def export(
        body: ExportRequest,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_scene_edit(
            db, lambda: CadSyncService(db, principal).export(body.location_id, body.format)
        )

    @router.get("/exports/{snapshot_id}/file")
    def download_export(
        snapshot_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        row = CadSyncService(db, principal).history(CadSnapshot, snapshot_id)
        return Response(
            row.source,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="cms-{row.id}.{row.format}"',
                "Cache-Control": "no-store",
            },
        )

    @router.get("/exports/{snapshot_id}/manifest")
    def download_manifest(
        snapshot_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return CadSyncService(db, principal).history(CadSnapshot, snapshot_id).manifest

    @router.post("/imports", status_code=201)
    async def stage(
        location_id: uuid.UUID,
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        try:
            source = await file.read(MAX_FILE_BYTES + 1)
            if len(source) > MAX_FILE_BYTES:
                raise ValidationError("CAD file exceeds the 8 MiB limit")
            filename = file.filename or ""
            format = filename.rsplit(".", 1)[-1].lower()
            return await run_in_threadpool(
                commit_scene_edit,
                db,
                lambda: CadSyncService(db, principal).stage(location_id, format, filename, source),
            )
        finally:
            await file.close()

    @router.get("/imports/{revision_id}")
    def preview(
        revision_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        service = CadSyncService(db, principal)
        return service.preview(service.history(CadImportRevision, revision_id))

    @router.get("/imports/{revision_id}/file")
    def download_original(
        revision_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        row = CadSyncService(db, principal).history(CadImportRevision, revision_id)
        return Response(
            row.source,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="revision-{row.id}.{row.format}"',
                "Cache-Control": "no-store",
            },
        )

    @router.post("/imports/{revision_id}/apply")
    def apply(
        revision_id: uuid.UUID,
        body: ApplyRequest,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_scene_edit(
            db, lambda: CadSyncService(db, principal).apply(revision_id, body.preview_token)
        )

    return router

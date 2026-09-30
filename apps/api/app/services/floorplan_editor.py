"""Versioned floor-plan operations with object-scope authorization.

Callers own the transaction. The service never commits. All mutable operations
require the current FloorPlan.version and create a new immutable revision rather
than modifying an older document in place.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from copy import deepcopy
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.floorplan_editor_models import FloorPlan, FloorPlanRevision
from app.models import Device, Location, Pathway, Project, Rack, Tenant, utcnow
from app.security import Principal, is_descendant_or_self, require_permission, resolve_principal


class FloorPlanService:
    MAX_OBJECTS = 5_000
    MAX_REVISIONS_PAGE = 500
    RESOURCE_MODELS = {
        "location": Location,
        "rack": Rack,
        "device": Device,
        "pathway": Pathway,
    }

    def __init__(self, session: Session, principal: Principal):
        self.db = session
        self.principal = principal
        self.tenant_id = principal.tenant_id
        if session.info.get("bypass_tenant"):
            raise AuthorizationError("Floor-plan operations cannot use a platform bypass session")
        if session.info.get("tenant_id") != self.tenant_id:
            raise AuthorizationError("Floor-plan operations require a matching tenant session")

    def _get(self, model, object_id: uuid.UUID):
        row = self.db.scalar(
            select(model)
            .where(
                model.id == object_id,
                model.tenant_id == self.tenant_id,
                model.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("Floor-plan resource or parent not found")
        return row

    def _authorize(self, permission: str, project_id: uuid.UUID, location_id: uuid.UUID):
        tenant = self.db.scalar(
            select(Tenant).where(Tenant.id == self.tenant_id, Tenant.active.is_(True))
        )
        if tenant is None:
            raise AuthorizationError("Tenant is inactive or unavailable")
        self._get(Project, project_id)
        self._get(Location, location_id)
        actual = resolve_principal(
            self.db,
            actor_id=self.principal.actor_id,
            tenant_id=self.tenant_id,
            project_id=project_id,
            location_id=location_id,
            request_id=self.principal.request_id,
            ip_address=self.principal.ip_address,
            user_agent=self.principal.user_agent,
        )
        require_permission(actual, permission)
        return actual

    @staticmethod
    def _finite(value: object, field: str, minimum: float, maximum: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"{field} must be a finite number")
        result = float(value)
        if not math.isfinite(result) or not minimum <= result <= maximum:
            raise ValidationError(f"{field} is outside the supported range")
        return result

    @staticmethod
    def _text(value: object, field: str, maximum: int, *, allow_empty: bool = False) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be text")
        result = value.strip()
        if (not result and not allow_empty) or len(result) > maximum:
            raise ValidationError(f"{field} has an invalid length")
        return result

    def _resource_location(self, kind: str, resource_id: uuid.UUID) -> uuid.UUID:
        model = self.RESOURCE_MODELS[kind]
        resource = self._get(model, resource_id)
        if isinstance(resource, Location):
            return resource.id
        location_id = getattr(resource, "location_id", None)
        if location_id is None:
            raise ValidationError(f"{kind} cannot be placed because it has no location")
        return location_id

    def _normalize_document(self, plan: FloorPlan, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValidationError("Floor-plan document must be an object")
        if raw.get("schema_version") != 1:
            raise ValidationError("Unsupported floor-plan schema_version")
        if raw.get("unit") != "mm":
            raise ValidationError("Floor-plan documents must use millimetres")
        canvas = raw.get("canvas")
        if not isinstance(canvas, dict):
            raise ValidationError("Floor-plan canvas is required")
        width = self._finite(canvas.get("width_mm"), "canvas.width_mm", 1, 1_000_000)
        height = self._finite(canvas.get("height_mm"), "canvas.height_mm", 1, 1_000_000)
        if abs(width - plan.width_mm) > 0.000001 or abs(height - plan.height_mm) > 0.000001:
            raise ValidationError("Document canvas dimensions must match the floor plan")
        grid = self._finite(canvas.get("grid_mm", 100), "canvas.grid_mm", 1, 100_000)
        snap = canvas.get("snap", True)
        if not isinstance(snap, bool):
            raise ValidationError("canvas.snap must be boolean")
        objects = raw.get("objects")
        if not isinstance(objects, list) or len(objects) > self.MAX_OBJECTS:
            raise ValidationError("Floor-plan objects must be a bounded list")

        normalized: list[dict[str, Any]] = []
        client_ids: set[str] = set()
        for index, item in enumerate(objects):
            if not isinstance(item, dict):
                raise ValidationError(f"objects[{index}] must be an object")
            client_id = self._text(item.get("client_id"), f"objects[{index}].client_id", 100)
            if client_id in client_ids:
                raise ValidationError("Duplicate floor-plan client_id")
            client_ids.add(client_id)
            kind = item.get("kind")
            if kind not in {*self.RESOURCE_MODELS, "annotation"}:
                raise ValidationError(f"Unsupported floor-plan object kind: {kind}")
            x = self._finite(item.get("x_mm"), "x_mm", 0, width)
            y = self._finite(item.get("y_mm"), "y_mm", 0, height)
            object_width = self._finite(item.get("width_mm", grid), "width_mm", 1, width)
            object_height = self._finite(item.get("height_mm", grid), "height_mm", 1, height)
            rotation = self._finite(item.get("rotation_deg", 0), "rotation_deg", -3600, 3600)
            z_index = item.get("z_index", index)
            if isinstance(z_index, bool) or not isinstance(z_index, int) or not -100_000 <= z_index <= 100_000:
                raise ValidationError("z_index must be a bounded integer")
            label = self._text(item.get("label", ""), "label", 180, allow_empty=True)
            resource_id: uuid.UUID | None = None
            if kind != "annotation":
                try:
                    resource_id = uuid.UUID(str(item.get("resource_id")))
                except (TypeError, ValueError, AttributeError) as exc:
                    raise ValidationError(f"objects[{index}].resource_id must be a UUID") from exc
                resource_location = self._resource_location(kind, resource_id)
                if not is_descendant_or_self(self.db, resource_location, plan.location_id):
                    raise AuthorizationError("Placed resource is outside the floor-plan location subtree")
            geometry = item.get("geometry", {})
            if not isinstance(geometry, dict):
                raise ValidationError("geometry must be an object")
            normalized.append(
                {
                    "client_id": client_id,
                    "kind": kind,
                    "resource_id": str(resource_id) if resource_id else None,
                    "label": label,
                    "x_mm": x,
                    "y_mm": y,
                    "width_mm": object_width,
                    "height_mm": object_height,
                    "rotation_deg": rotation,
                    "z_index": z_index,
                    "geometry": deepcopy(geometry),
                }
            )
        normalized.sort(key=lambda row: (row["z_index"], row["client_id"]))
        return {
            "schema_version": 1,
            "unit": "mm",
            "canvas": {
                "width_mm": width,
                "height_mm": height,
                "grid_mm": grid,
                "snap": snap,
            },
            "objects": normalized,
        }

    @staticmethod
    def _checksum(document: dict[str, Any]) -> str:
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _revision(self, plan_id: uuid.UUID, number: int) -> FloorPlanRevision:
        row = self.db.scalar(
            select(FloorPlanRevision).where(
                FloorPlanRevision.tenant_id == self.tenant_id,
                FloorPlanRevision.floor_plan_id == plan_id,
                FloorPlanRevision.revision == number,
                FloorPlanRevision.deleted_at.is_(None),
            )
        )
        if row is None:
            raise NotFoundError("Floor-plan revision not found")
        return row

    def _advance(self, plan: FloorPlan, expected_version: int, **values: Any) -> int:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise ValidationError("A positive expected_version is required")
        result = self.db.execute(
            update(FloorPlan)
            .where(
                FloorPlan.id == plan.id,
                FloorPlan.tenant_id == self.tenant_id,
                FloorPlan.deleted_at.is_(None),
                FloorPlan.version == expected_version,
            )
            .values(version=FloorPlan.version + 1, updated_at=utcnow(), **values)
        )
        if result.rowcount != 1:
            raise ConflictError("Floor plan changed; reload before retrying")
        return expected_version + 1

    def create_plan(
        self,
        *,
        project_id: uuid.UUID,
        location_id: uuid.UUID,
        name: str,
        width_mm: float,
        height_mm: float,
        grid_mm: float = 100.0,
        background_object_key: str | None = None,
    ) -> dict[str, Any]:
        principal = self._authorize("floor_plan:write", project_id, location_id)
        name = self._text(name, "name", 180)
        width = self._finite(width_mm, "width_mm", 1, 1_000_000)
        height = self._finite(height_mm, "height_mm", 1, 1_000_000)
        grid = self._finite(grid_mm, "grid_mm", 1, 100_000)
        background = None
        if background_object_key is not None:
            background = self._text(background_object_key, "background_object_key", 500)
        plan = FloorPlan(
            tenant_id=self.tenant_id,
            project_id=project_id,
            location_id=location_id,
            name=name,
            unit="mm",
            width_mm=width,
            height_mm=height,
            head_revision=1,
            status="draft",
            background_object_key=background,
        )
        self.db.add(plan)
        self.db.flush()
        document = {
            "schema_version": 1,
            "unit": "mm",
            "canvas": {"width_mm": width, "height_mm": height, "grid_mm": grid, "snap": True},
            "objects": [],
        }
        revision = FloorPlanRevision(
            tenant_id=self.tenant_id,
            floor_plan_id=plan.id,
            revision=1,
            schema_version=1,
            document=document,
            checksum_sha256=self._checksum(document),
            note="Initial revision",
            created_by=principal.actor_id,
        )
        self.db.add(revision)
        self.db.flush()
        record_audit(
            self.db,
            principal=principal,
            action="floor_plan.created",
            object_type="floor_plan",
            object_id=plan.id,
            after={"name": name, "revision": 1, "location_id": str(location_id)},
            project_id=project_id,
        )
        return self.get_plan(plan.id)

    def _scoped_plan(self, plan_id: uuid.UUID, permission: str) -> tuple[FloorPlan, Principal]:
        plan = self._get(FloorPlan, plan_id)
        principal = self._authorize(permission, plan.project_id, plan.location_id)
        return plan, principal

    def get_plan(self, plan_id: uuid.UUID, view: Literal["working", "published"] = "working"):
        plan, _ = self._scoped_plan(plan_id, "floor_plan:read")
        revision_number = plan.head_revision
        if view == "published":
            if plan.published_revision is None:
                raise NotFoundError("Floor plan has no published revision")
            revision_number = plan.published_revision
        revision = self._revision(plan.id, revision_number)
        return {
            "id": str(plan.id),
            "project_id": str(plan.project_id),
            "location_id": str(plan.location_id),
            "name": plan.name,
            "version": plan.version,
            "status": plan.status,
            "width_mm": plan.width_mm,
            "height_mm": plan.height_mm,
            "head_revision": plan.head_revision,
            "published_revision": plan.published_revision,
            "background_object_key": plan.background_object_key,
            "revision": revision.revision,
            "checksum_sha256": revision.checksum_sha256,
            "document": deepcopy(revision.document),
        }

    def list_plans(self, project_id: uuid.UUID, location_id: uuid.UUID, limit: int = 100):
        self._authorize("floor_plan:read", project_id, location_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValidationError("Invalid floor-plan list limit")
        rows = self.db.scalars(
            select(FloorPlan)
            .where(
                FloorPlan.tenant_id == self.tenant_id,
                FloorPlan.project_id == project_id,
                FloorPlan.location_id == location_id,
                FloorPlan.deleted_at.is_(None),
            )
            .order_by(FloorPlan.name, FloorPlan.id)
            .limit(limit + 1)
        ).all()
        return {
            "items": [
                {
                    "id": str(row.id),
                    "name": row.name,
                    "version": row.version,
                    "status": row.status,
                    "head_revision": row.head_revision,
                    "published_revision": row.published_revision,
                }
                for row in rows[:limit]
            ],
            "truncated": len(rows) > limit,
        }

    def save_revision(
        self,
        plan_id: uuid.UUID,
        expected_version: int,
        document: dict[str, Any],
        note: str = "",
    ):
        plan, principal = self._scoped_plan(plan_id, "floor_plan:write")
        normalized = self._normalize_document(plan, document)
        note = self._text(note, "note", 500, allow_empty=True)
        next_revision = plan.head_revision + 1
        next_version = self._advance(plan, expected_version, head_revision=next_revision, status="draft")
        revision = FloorPlanRevision(
            tenant_id=self.tenant_id,
            floor_plan_id=plan.id,
            revision=next_revision,
            schema_version=1,
            document=normalized,
            checksum_sha256=self._checksum(normalized),
            note=note,
            created_by=principal.actor_id,
        )
        self.db.add(revision)
        self.db.flush()
        record_audit(
            self.db,
            principal=principal,
            action="floor_plan.revision_saved",
            object_type="floor_plan",
            object_id=plan.id,
            after={"revision": next_revision, "checksum": revision.checksum_sha256},
            project_id=plan.project_id,
        )
        return {
            "id": str(plan.id),
            "version": next_version,
            "revision": next_revision,
            "checksum_sha256": revision.checksum_sha256,
        }

    def publish(self, plan_id: uuid.UUID, revision: int, expected_version: int):
        plan, principal = self._scoped_plan(plan_id, "floor_plan:publish")
        selected = self._revision(plan.id, revision)
        next_version = self._advance(
            plan,
            expected_version,
            published_revision=selected.revision,
            status="published",
        )
        record_audit(
            self.db,
            principal=principal,
            action="floor_plan.published",
            object_type="floor_plan",
            object_id=plan.id,
            after={"revision": selected.revision, "checksum": selected.checksum_sha256},
            project_id=plan.project_id,
        )
        return {"id": str(plan.id), "version": next_version, "published_revision": selected.revision}

    def restore(self, plan_id: uuid.UUID, source_revision: int, expected_version: int, note: str = ""):
        plan, principal = self._scoped_plan(plan_id, "floor_plan:write")
        source = self._revision(plan.id, source_revision)
        next_revision = plan.head_revision + 1
        next_version = self._advance(plan, expected_version, head_revision=next_revision, status="draft")
        normalized = self._normalize_document(plan, deepcopy(source.document))
        revision = FloorPlanRevision(
            tenant_id=self.tenant_id,
            floor_plan_id=plan.id,
            revision=next_revision,
            schema_version=1,
            document=normalized,
            checksum_sha256=self._checksum(normalized),
            note=self._text(note, "note", 500, allow_empty=True) or f"Restored revision {source_revision}",
            created_by=principal.actor_id,
            restored_from_revision=source_revision,
        )
        self.db.add(revision)
        self.db.flush()
        record_audit(
            self.db,
            principal=principal,
            action="floor_plan.restored",
            object_type="floor_plan",
            object_id=plan.id,
            after={"revision": next_revision, "restored_from": source_revision},
            project_id=plan.project_id,
        )
        return {
            "id": str(plan.id),
            "version": next_version,
            "revision": next_revision,
            "restored_from_revision": source_revision,
            "checksum_sha256": revision.checksum_sha256,
        }

    def list_revisions(self, plan_id: uuid.UUID, limit: int = 100):
        plan, _ = self._scoped_plan(plan_id, "floor_plan:read")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.MAX_REVISIONS_PAGE:
            raise ValidationError("Invalid revision list limit")
        rows = self.db.scalars(
            select(FloorPlanRevision)
            .where(
                FloorPlanRevision.tenant_id == self.tenant_id,
                FloorPlanRevision.floor_plan_id == plan.id,
                FloorPlanRevision.deleted_at.is_(None),
            )
            .order_by(FloorPlanRevision.revision.desc())
            .limit(limit + 1)
        ).all()
        return {
            "items": [
                {
                    "revision": row.revision,
                    "checksum_sha256": row.checksum_sha256,
                    "note": row.note,
                    "created_by": str(row.created_by),
                    "created_at": row.created_at.isoformat(),
                    "restored_from_revision": row.restored_from_revision,
                    "published": row.revision == plan.published_revision,
                }
                for row in rows[:limit]
            ],
            "truncated": len(rows) > limit,
        }

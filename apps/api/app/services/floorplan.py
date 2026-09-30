"""Tenant- and scope-authorized immutable Floor Plan revision service."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.floorplan_models import FloorPlan, FloorPlanRevision
from app.models import Device, Location, Pathway, Project, Rack, Tenant, utcnow
from app.security import Principal, is_descendant_or_self, require_permission, resolve_principal


class FloorPlanService:
    MAX_DOCUMENT_BYTES = 2_000_000
    MAX_OBJECTS = 2_000
    MAX_PATHS = 1_000
    MAX_POINTS = 20_000
    OBJECT_MODELS = {
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
            raise AuthorizationError("Floor Plan operations cannot use a platform bypass session")
        if session.info.get("tenant_id") != self.tenant_id:
            raise AuthorizationError("Floor Plan operations require a matching tenant session")

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
            raise NotFoundError("Floor Plan resource or parent not found")
        return row

    def _authorize(
        self,
        permission: str,
        project_id: uuid.UUID,
        location_id: uuid.UUID,
    ) -> Principal:
        tenant = self.db.scalar(
            select(Tenant).where(
                Tenant.id == self.tenant_id,
                Tenant.active.is_(True),
            )
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
    def _clean_name(value: str, *, field: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be text")
        clean = value.strip()
        if not 1 <= len(clean) <= maximum:
            raise ValidationError(f"{field} must contain 1 to {maximum} characters")
        return clean

    @staticmethod
    def _finite(
        value: Any,
        *,
        field: str,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"{field} must be a finite number")
        number = float(value)
        if not math.isfinite(number):
            raise ValidationError(f"{field} must be a finite number")
        if minimum is not None and number < minimum:
            raise ValidationError(f"{field} must be at least {minimum}")
        if maximum is not None and number > maximum:
            raise ValidationError(f"{field} must be at most {maximum}")
        return number

    @staticmethod
    def _uuid(value: Any, *, field: str) -> uuid.UUID:
        try:
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            raise ValidationError(f"{field} must be a UUID") from None

    @staticmethod
    def _canonical(document: dict[str, Any]) -> tuple[dict[str, Any], str, int]:
        try:
            encoded = json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise ValidationError("Floor Plan document must be valid finite JSON") from None
        checksum = hashlib.sha256(encoded).hexdigest()
        return json.loads(encoded), checksum, len(encoded)

    def _resource_location(self, object_type: str, object_id: uuid.UUID) -> uuid.UUID:
        model = self.OBJECT_MODELS.get(object_type)
        if model is None:
            raise ValidationError(f"Unsupported Floor Plan object type: {object_type}")
        resource = self._get(model, object_id)
        if isinstance(resource, Location):
            return resource.id
        return resource.location_id

    def _validate_document(
        self,
        plan: FloorPlan,
        document: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if not isinstance(document, dict):
            raise ValidationError("Floor Plan document must be an object")
        schema_version = document.get("schema_version")
        if schema_version != 1 or isinstance(schema_version, bool):
            raise ValidationError("Floor Plan schema_version must equal 1")
        grid_size = self._finite(
            document.get("grid_size", 10),
            field="grid_size",
            minimum=0.01,
            maximum=max(plan.canvas_width, plan.canvas_height),
        )
        objects = document.get("objects", [])
        paths = document.get("paths", [])
        if not isinstance(objects, list) or len(objects) > self.MAX_OBJECTS:
            raise ValidationError(f"objects must be a list of at most {self.MAX_OBJECTS} items")
        if not isinstance(paths, list) or len(paths) > self.MAX_PATHS:
            raise ValidationError(f"paths must be a list of at most {self.MAX_PATHS} items")

        normalized_objects: list[dict[str, Any]] = []
        seen_resources: set[tuple[str, uuid.UUID]] = set()
        seen_ids: set[str] = set()
        for index, item in enumerate(objects):
            if not isinstance(item, dict):
                raise ValidationError(f"objects[{index}] must be an object")
            item_id = self._clean_name(
                item.get("id", ""),
                field=f"objects[{index}].id",
                maximum=100,
            )
            if item_id in seen_ids:
                raise ValidationError(f"Duplicate drawing object id: {item_id}")
            seen_ids.add(item_id)
            object_type = item.get("object_type")
            object_id = self._uuid(
                item.get("object_id"),
                field=f"objects[{index}].object_id",
            )
            key = (str(object_type), object_id)
            if key in seen_resources:
                raise ValidationError("A physical resource may appear only once in a revision")
            seen_resources.add(key)
            actual_location = self._resource_location(str(object_type), object_id)
            if not is_descendant_or_self(self.db, actual_location, plan.location_id):
                raise AuthorizationError("Drawing object is outside the Floor Plan location scope")
            x = self._finite(item.get("x"), field=f"objects[{index}].x", minimum=0)
            y = self._finite(item.get("y"), field=f"objects[{index}].y", minimum=0)
            width = self._finite(
                item.get("width"),
                field=f"objects[{index}].width",
                minimum=0.01,
            )
            height = self._finite(
                item.get("height"),
                field=f"objects[{index}].height",
                minimum=0.01,
            )
            if x + width > plan.canvas_width or y + height > plan.canvas_height:
                raise ValidationError("Drawing object exceeds the Floor Plan canvas")
            rotation = self._finite(
                item.get("rotation", 0),
                field=f"objects[{index}].rotation",
                minimum=-360,
                maximum=360,
            )
            z_index = item.get("z_index", index)
            if isinstance(z_index, bool) or not isinstance(z_index, int) or not -10_000 <= z_index <= 10_000:
                raise ValidationError(f"objects[{index}].z_index is invalid")
            locked = item.get("locked", False)
            if not isinstance(locked, bool):
                raise ValidationError(f"objects[{index}].locked must be boolean")
            label = item.get("label")
            if label is not None:
                label = self._clean_name(
                    label,
                    field=f"objects[{index}].label",
                    maximum=180,
                )
            normalized_objects.append(
                {
                    "id": item_id,
                    "object_type": str(object_type),
                    "object_id": str(object_id),
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "rotation": rotation,
                    "z_index": z_index,
                    "locked": locked,
                    "label": label,
                }
            )

        normalized_paths: list[dict[str, Any]] = []
        point_count = 0
        for index, item in enumerate(paths):
            if not isinstance(item, dict):
                raise ValidationError(f"paths[{index}] must be an object")
            item_id = self._clean_name(
                item.get("id", ""),
                field=f"paths[{index}].id",
                maximum=100,
            )
            if item_id in seen_ids:
                raise ValidationError(f"Duplicate drawing object id: {item_id}")
            seen_ids.add(item_id)
            pathway_id = self._uuid(
                item.get("pathway_id"),
                field=f"paths[{index}].pathway_id",
            )
            actual_location = self._resource_location("pathway", pathway_id)
            if not is_descendant_or_self(self.db, actual_location, plan.location_id):
                raise AuthorizationError("Pathway is outside the Floor Plan location scope")
            points = item.get("points")
            if not isinstance(points, list) or len(points) < 2:
                raise ValidationError(f"paths[{index}].points must contain at least two points")
            point_count += len(points)
            if point_count > self.MAX_POINTS:
                raise ValidationError(f"Floor Plan has more than {self.MAX_POINTS} path points")
            normalized_points = []
            for point_index, point in enumerate(points):
                if not isinstance(point, dict):
                    raise ValidationError("Path points must be objects")
                px = self._finite(
                    point.get("x"),
                    field=f"paths[{index}].points[{point_index}].x",
                    minimum=0,
                    maximum=plan.canvas_width,
                )
                py = self._finite(
                    point.get("y"),
                    field=f"paths[{index}].points[{point_index}].y",
                    minimum=0,
                    maximum=plan.canvas_height,
                )
                normalized_points.append({"x": px, "y": py})
            width = self._finite(
                item.get("width", 1),
                field=f"paths[{index}].width",
                minimum=0.01,
                maximum=max(plan.canvas_width, plan.canvas_height),
            )
            normalized_paths.append(
                {
                    "id": item_id,
                    "pathway_id": str(pathway_id),
                    "points": normalized_points,
                    "width": width,
                    "label": str(item.get("label", ""))[:180],
                }
            )

        normalized = {
            "schema_version": 1,
            "grid_size": grid_size,
            "objects": sorted(
                normalized_objects,
                key=lambda row: (row["z_index"], row["id"]),
            ),
            "paths": sorted(normalized_paths, key=lambda row: row["id"]),
        }
        canonical, checksum, size = self._canonical(normalized)
        if size > self.MAX_DOCUMENT_BYTES:
            raise ValidationError(
                f"Floor Plan document exceeds {self.MAX_DOCUMENT_BYTES} bytes"
            )
        return canonical, checksum

    @staticmethod
    def _empty_document(grid_size: float = 10.0) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "grid_size": grid_size,
            "objects": [],
            "paths": [],
        }

    def _revision(self, plan: FloorPlan, revision_number: int) -> FloorPlanRevision:
        revision = self.db.scalar(
            select(FloorPlanRevision).where(
                FloorPlanRevision.tenant_id == self.tenant_id,
                FloorPlanRevision.floor_plan_id == plan.id,
                FloorPlanRevision.revision_number == revision_number,
                FloorPlanRevision.deleted_at.is_(None),
            )
        )
        if revision is None:
            raise ConflictError("Floor Plan revision history is incomplete")
        return revision

    def _advance(
        self,
        plan: FloorPlan,
        expected_version: int,
        *,
        current_revision_number: int | None = None,
        published_revision_number: int | None | object = ...,
        status: str | None = None,
        published_at: Any = ...,
    ) -> FloorPlan:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise ValidationError("A positive expected_version is required")
        values: dict[str, Any] = {
            "version": FloorPlan.version + 1,
            "updated_at": utcnow(),
        }
        if current_revision_number is not None:
            values["current_revision_number"] = current_revision_number
        if published_revision_number is not ...:
            values["published_revision_number"] = published_revision_number
        if status is not None:
            values["status"] = status
        if published_at is not ...:
            values["published_at"] = published_at
        result = self.db.execute(
            update(FloorPlan)
            .where(
                FloorPlan.id == plan.id,
                FloorPlan.tenant_id == self.tenant_id,
                FloorPlan.deleted_at.is_(None),
                FloorPlan.version == expected_version,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise ConflictError("Floor Plan changed; reload before retrying")
        self.db.flush()
        self.db.expire(plan)
        return self._get(FloorPlan, plan.id)

    def _serialize_revision(self, revision: FloorPlanRevision) -> dict[str, Any]:
        return {
            "id": str(revision.id),
            "revision_number": revision.revision_number,
            "schema_version": revision.schema_version,
            "checksum_sha256": revision.checksum_sha256,
            "change_summary": revision.change_summary,
            "created_by": str(revision.created_by),
            "restored_from_revision_id": (
                str(revision.restored_from_revision_id)
                if revision.restored_from_revision_id
                else None
            ),
            "created_at": revision.created_at.isoformat(),
        }

    def _serialize_plan(
        self,
        plan: FloorPlan,
        *,
        include_document: bool,
    ) -> dict[str, Any]:
        current = self._revision(plan, plan.current_revision_number)
        result = {
            "id": str(plan.id),
            "project_id": str(plan.project_id),
            "location_id": str(plan.location_id),
            "name": plan.name,
            "units": plan.units,
            "canvas_width": plan.canvas_width,
            "canvas_height": plan.canvas_height,
            "background_reference": plan.background_reference,
            "status": plan.status,
            "version": plan.version,
            "current_revision_number": plan.current_revision_number,
            "published_revision_number": plan.published_revision_number,
            "published_at": plan.published_at.isoformat() if plan.published_at else None,
            "current_revision": self._serialize_revision(current),
        }
        if include_document:
            result["document"] = deepcopy(current.document)
        return result

    def create_plan(
        self,
        *,
        project_id: uuid.UUID,
        location_id: uuid.UUID,
        name: str,
        units: str,
        canvas_width: float,
        canvas_height: float,
        background_reference: str | None = None,
        document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._authorize("floor_plan:write", project_id, location_id)
        name = self._clean_name(name, field="name", maximum=180)
        if units not in {"mm", "m", "ft"}:
            raise ValidationError("units must be mm, m or ft")
        width = self._finite(
            canvas_width,
            field="canvas_width",
            minimum=0.01,
            maximum=1_000_000,
        )
        height = self._finite(
            canvas_height,
            field="canvas_height",
            minimum=0.01,
            maximum=1_000_000,
        )
        background = None
        if background_reference is not None:
            background = self._clean_name(
                background_reference,
                field="background_reference",
                maximum=500,
            )
        plan = FloorPlan(
            tenant_id=self.tenant_id,
            project_id=project_id,
            location_id=location_id,
            name=name,
            units=units,
            canvas_width=width,
            canvas_height=height,
            background_reference=background,
            status="draft",
            current_revision_number=1,
        )
        self.db.add(plan)
        self.db.flush()
        normalized, checksum = self._validate_document(
            plan,
            document or self._empty_document(),
        )
        revision = FloorPlanRevision(
            tenant_id=self.tenant_id,
            floor_plan_id=plan.id,
            revision_number=1,
            schema_version=1,
            document=normalized,
            checksum_sha256=checksum,
            change_summary="Initial revision",
            created_by=self.principal.actor_id,
        )
        self.db.add(revision)
        self.db.flush()
        record_audit(
            self.db,
            principal=self.principal,
            action="floorplan.created",
            object_type="floor_plan",
            object_id=plan.id,
            after={
                "name": plan.name,
                "location_id": str(plan.location_id),
                "revision": 1,
                "checksum": checksum,
            },
            project_id=project_id,
        )
        return self._serialize_plan(plan, include_document=True)

    def get_plan(self, plan_id: uuid.UUID) -> dict[str, Any]:
        plan = self._get(FloorPlan, plan_id)
        self._authorize("floor_plan:read", plan.project_id, plan.location_id)
        return self._serialize_plan(plan, include_document=True)

    def list_plans(
        self,
        *,
        project_id: uuid.UUID,
        location_id: uuid.UUID,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._authorize("floor_plan:read", project_id, location_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100")
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
                self._serialize_plan(row, include_document=False)
                for row in rows[:limit]
            ],
            "truncated": len(rows) > limit,
        }

    def save_revision(
        self,
        plan_id: uuid.UUID,
        *,
        expected_version: int,
        document: dict[str, Any],
        change_summary: str = "",
    ) -> dict[str, Any]:
        plan = self._get(FloorPlan, plan_id)
        self._authorize("floor_plan:write", plan.project_id, plan.location_id)
        summary = change_summary.strip()
        if len(summary) > 500:
            raise ValidationError("change_summary may contain at most 500 characters")
        normalized, checksum = self._validate_document(plan, document)
        current = self._revision(plan, plan.current_revision_number)
        if current.checksum_sha256 == checksum:
            raise ConflictError("Floor Plan document is unchanged")
        next_number = plan.current_revision_number + 1
        self._advance(
            plan,
            expected_version,
            current_revision_number=next_number,
            status="draft",
        )
        revision = FloorPlanRevision(
            tenant_id=self.tenant_id,
            floor_plan_id=plan.id,
            revision_number=next_number,
            schema_version=1,
            document=normalized,
            checksum_sha256=checksum,
            change_summary=summary,
            created_by=self.principal.actor_id,
        )
        self.db.add(revision)
        self.db.flush()
        record_audit(
            self.db,
            principal=self.principal,
            action="floorplan.revision.created",
            object_type="floor_plan",
            object_id=plan.id,
            before={
                "revision": current.revision_number,
                "checksum": current.checksum_sha256,
            },
            after={"revision": next_number, "checksum": checksum},
            project_id=plan.project_id,
        )
        return self._serialize_plan(plan, include_document=True)

    def publish(self, plan_id: uuid.UUID, *, expected_version: int) -> dict[str, Any]:
        plan = self._get(FloorPlan, plan_id)
        self._authorize("floor_plan:publish", plan.project_id, plan.location_id)
        current = self._revision(plan, plan.current_revision_number)
        self._advance(
            plan,
            expected_version,
            published_revision_number=plan.current_revision_number,
            status="published",
            published_at=utcnow(),
        )
        record_audit(
            self.db,
            principal=self.principal,
            action="floorplan.published",
            object_type="floor_plan",
            object_id=plan.id,
            after={
                "revision": plan.current_revision_number,
                "checksum": current.checksum_sha256,
            },
            project_id=plan.project_id,
        )
        return self._serialize_plan(plan, include_document=True)

    def revisions(self, plan_id: uuid.UUID, *, limit: int = 100) -> dict[str, Any]:
        plan = self._get(FloorPlan, plan_id)
        self._authorize("floor_plan:read", plan.project_id, plan.location_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100")
        rows = self.db.scalars(
            select(FloorPlanRevision)
            .where(
                FloorPlanRevision.tenant_id == self.tenant_id,
                FloorPlanRevision.floor_plan_id == plan.id,
                FloorPlanRevision.deleted_at.is_(None),
            )
            .order_by(FloorPlanRevision.revision_number.desc())
            .limit(limit + 1)
        ).all()
        return {
            "items": [self._serialize_revision(row) for row in rows[:limit]],
            "truncated": len(rows) > limit,
        }

    def restore(
        self,
        plan_id: uuid.UUID,
        revision_id: uuid.UUID,
        *,
        expected_version: int,
        change_summary: str = "",
    ) -> dict[str, Any]:
        plan = self._get(FloorPlan, plan_id)
        self._authorize("floor_plan:write", plan.project_id, plan.location_id)
        source = self._get(FloorPlanRevision, revision_id)
        if source.floor_plan_id != plan.id:
            raise NotFoundError("Revision does not belong to the Floor Plan")
        current = self._revision(plan, plan.current_revision_number)
        next_number = plan.current_revision_number + 1
        self._advance(
            plan,
            expected_version,
            current_revision_number=next_number,
            status="draft",
        )
        summary = change_summary.strip() or f"Restore revision {source.revision_number}"
        if len(summary) > 500:
            raise ValidationError("change_summary may contain at most 500 characters")
        revision = FloorPlanRevision(
            tenant_id=self.tenant_id,
            floor_plan_id=plan.id,
            revision_number=next_number,
            schema_version=source.schema_version,
            document=deepcopy(source.document),
            checksum_sha256=source.checksum_sha256,
            change_summary=summary,
            created_by=self.principal.actor_id,
            restored_from_revision_id=source.id,
        )
        self.db.add(revision)
        self.db.flush()
        record_audit(
            self.db,
            principal=self.principal,
            action="floorplan.revision.restored",
            object_type="floor_plan",
            object_id=plan.id,
            before={
                "revision": current.revision_number,
                "checksum": current.checksum_sha256,
            },
            after={
                "revision": next_number,
                "restored_from": source.revision_number,
                "checksum": source.checksum_sha256,
            },
            project_id=plan.project_id,
        )
        return self._serialize_plan(plan, include_document=True)

"""Reviewed file exchange: immutable proposals, fresh scoped diffs, one transaction."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import PurePath

from sqlalchemy import update

from app.audit import record_audit
from app.cad_models import CadApplication, CadImportRevision, CadSnapshot
from app.exceptions import AuthorizationError, ConflictError, ValidationError
from app.models import CableRouteSegment, Pathway, PathwaySegment, Rack, utcnow
from app.schemas_scene_editor import RackPoseUpdate
from app.services.cad_formats import export_file, parse_file, require_format
from app.services.cad_geometry import digest
from app.services.cad_manifest import manifest
from app.services.connectivity import ConnectivityService
from app.services.scene_editor import SceneEditorService


class CadSyncService:
    def __init__(self, db, principal):
        self.db = db
        self.editor = SceneEditorService(db, principal)

    def scope(self, location_id):
        self.editor._scope(location_id, "report:export")

    def history(self, model, object_id):
        row = self.editor._get(model, object_id)
        self.scope(row.location_id)
        if row.project_id != self.editor.principal.project_id:
            raise AuthorizationError("CAD history belongs to another workspace")
        return row

    def export(self, location_id, format):
        require_format(format)
        self.scope(location_id)
        snapshot_id = uuid.uuid4()
        document = manifest(self.editor, location_id, snapshot_id)
        source = export_file(format, document)
        readonly_signature = digest(parse_file(format, source).get("readonly"))
        # Native conversion holds no writer lock. Under a short final lock,
        # reject any scene changes since capture rather than export a torn view.
        self.editor._lock_location(location_id)
        self.db.expire_all()
        self.scope(location_id)
        if digest(manifest(self.editor, location_id, snapshot_id)) != digest(document):
            raise ConflictError("Scene changed during CAD export; retry from a fresh snapshot")
        document["readonly_signature"] = readonly_signature
        row = CadSnapshot(
            id=snapshot_id,
            tenant_id=self.editor.principal.tenant_id,
            location_id=location_id,
            project_id=self.editor.principal.project_id,
            format=format,
            manifest=document,
            source=source,
            sha256=hashlib.sha256(source).hexdigest(),
        )
        self.db.add(row)
        self.db.flush()
        self.audit("cad.exported", row.id, {"format": format, "sha256": row.sha256})
        return {
            "id": str(row.id),
            "format": format,
            "filename": f"cms-{row.id}.{format}",
            "warnings": document["warnings"],
            "limitations": document["limitations"],
        }

    def stage(self, location_id, format, filename, source):
        require_format(format)
        self.scope(location_id)
        sha = hashlib.sha256(source).hexdigest()
        fingerprint = digest([str(location_id), str(self.editor.principal.project_id), format, sha])
        existing = self.db.scalar(
            self.editor.scene._query(CadImportRevision).where(
                CadImportRevision.fingerprint == fingerprint
            )
        )
        if existing:
            return self.preview(existing)
        try:
            parsed = parse_file(format, source)
        except ValidationError as error:
            parsed = {"error": str(error), "objects": [], "snapshot_id": None}
        # Parsing never holds a database writer lock. Recheck access and duplicate
        # identity after parsing, under the same serialization lock as Apply.
        self.editor._lock_location(location_id)
        self.scope(location_id)
        existing = self.db.scalar(
            self.editor.scene._query(CadImportRevision).where(
                CadImportRevision.fingerprint == fingerprint
            )
        )
        if existing:
            return self.preview(existing)
        snapshot_id = None
        if parsed.get("snapshot_id"):
            # Unknown/foreign IDs do not select or overwrite any business object.
            candidate = self.db.scalar(
                self.editor.scene._query(CadSnapshot).where(
                    CadSnapshot.id == uuid.UUID(parsed["snapshot_id"])
                )
            )
            if (
                candidate is None
                or candidate.location_id != location_id
                or candidate.project_id != self.editor.principal.project_id
                or candidate.format != format
            ):
                parsed = {
                    "error": "Export snapshot is unavailable in this room/workspace",
                    "objects": [],
                }
            else:
                snapshot_id = candidate.id
        row = CadImportRevision(
            tenant_id=self.editor.principal.tenant_id,
            location_id=location_id,
            project_id=self.editor.principal.project_id,
            snapshot_id=snapshot_id,
            format=format,
            filename=PurePath(filename.replace("\\", "/")).name[:240] or f"import.{format}",
            source=source,
            sha256=sha,
            fingerprint=fingerprint,
            parsed=parsed,
        )
        self.db.add(row)
        self.db.flush()
        self.audit("cad.staged", row.id, {"format": format, "sha256": sha})
        return self.preview(row)

    def receipt(self, revision_id):
        return self.db.scalar(
            self.editor.scene._query(CadApplication).where(
                CadApplication.revision_id == revision_id
            )
        )

    def references(self, segment_id):
        return list(
            self.db.scalars(
                self.editor.scene._query(CableRouteSegment).where(
                    CableRouteSegment.pathway_segment_id == uuid.UUID(segment_id)
                )
            ).all()
        )

    def preview(self, revision):
        self.scope(revision.location_id)
        result = {
            "id": str(revision.id),
            "filename": revision.filename,
            "format": revision.format,
            "sha256": revision.sha256,
            "diffs": [],
            "can_apply": False,
            "applied": False,
            "references": revision.parsed.get("references", 0),
        }
        receipt = self.receipt(revision.id)
        if receipt:
            return {**result, "applied": True, "receipt": receipt.result}
        error = revision.parsed.get("error")
        if error or not revision.snapshot_id:
            return {
                **result,
                "error": error
                or "No stable export mapping: reference geometry only; no objects will be created",
            }
        snapshot = self.history(CadSnapshot, revision.snapshot_id)
        if digest(revision.parsed.get("readonly")) != snapshot.manifest.get("readonly_signature"):
            return {
                **result,
                "error": "CAD 名称、身份属性、端口归属或连接关系发生变化；本版本不回写这些字段，请保留原定义。",
            }
        current = manifest(self.editor, revision.location_id, snapshot.id)
        base = {o["id"]: o for o in snapshot.manifest["objects"]}
        incoming = {o["id"]: o for o in revision.parsed["objects"]}
        present = {o["id"]: o for o in current["objects"]}
        for object_id in sorted(base.keys() | incoming.keys()):
            old, new, live = base.get(object_id), incoming.get(object_id), present.get(object_id)
            status, reason = "unchanged", "Geometry is unchanged"
            if not old:
                status, reason = (
                    "blocked",
                    "ID is not in this export snapshot; manual mapping is required",
                )
            elif not new:
                status, reason = (
                    "blocked",
                    "Exported object is missing; implicit deletion is forbidden",
                )
            elif new["kind"] != old["kind"] or new["version"] != old["version"]:
                status, reason = (
                    "blocked",
                    "Stable identity or baseline version metadata was altered",
                )
            elif not live:
                status, reason = "conflict", "Object no longer exists in the current scope"
            elif digest(new["geometry"]) == digest(old["geometry"]):
                if live["version"] != old["version"]:
                    status, reason = (
                        "server_changed",
                        "CAD did not edit this object; current system value is retained",
                    )
            elif digest(new["geometry"]) == digest(live["geometry"]):
                status, reason = "already_current", "Incoming geometry already matches the system"
            elif old["kind"] in {"device", "port"}:
                status, reason = (
                    "reference",
                    "Derived display geometry is not applied; device identity and port anchors remain system-controlled",
                )
            elif live["version"] != old["version"] or digest(live["geometry"]) != digest(
                old["geometry"]
            ):
                status, reason = "conflict", "Both CAD and the system changed since this export"
            elif old["kind"] == "rack" and new["geometry"]["size"] == old["geometry"]["size"]:
                status, reason = (
                    "update",
                    "Update rack position/rotation; no automatic cable re-routing or measured-length changes",
                )
            elif (
                old["kind"] == "pathway_segment"
                and new["geometry"]["length_m"] == old["geometry"]["length_m"]
            ):
                if self.references(object_id):
                    status, reason = (
                        "blocked",
                        "Cable routes reference this tray; their offsets/hash must be resolved before geometry can change",
                    )
                else:
                    status, reason = (
                        "update",
                        "Update unused tray geometry; registered length remains unchanged",
                    )
            else:
                status, reason = (
                    "blocked",
                    "This geometry/property change is outside v1 (room size, proxy shape, cable routes or registered length)",
                )
            result["diffs"].append(
                {
                    "id": object_id,
                    "kind": (old or new)["kind"],
                    "identifier": (old or {}).get("identifier", object_id),
                    "status": status,
                    "reason": reason,
                    "base": old["geometry"] if old else None,
                    "incoming": new["geometry"] if new else None,
                    "current": live["geometry"] if live else None,
                    "current_version": live["version"] if live else None,
                }
            )
        # v1 uses existing per-rack move semantics. Make intermediate collisions
        # explicit in preview, including otherwise-legal simultaneous rack swaps.
        poses = {
            str(row.id): self.editor._pose(row)
            for row in self.db.scalars(
                self.editor.scene._query(Rack).where(Rack.location_id == revision.location_id)
            )
        }
        dimensions = present[str(revision.location_id)]["geometry"]["size"]
        for item in result["diffs"]:
            if item["status"] == "update" and item["kind"] == "pathway_segment":
                if any(
                    v < 0 or v > limit
                    for p in item["incoming"]["points"]
                    for v, limit in zip(p, dimensions)
                ):
                    item.update(status="blocked", reason="线槽顶点超出房间边界，不能应用。")
            if item["status"] != "update" or item["kind"] != "rack":
                continue
            geometry = item["incoming"]
            candidate = {
                **poses[item["id"]],
                **dict(zip(("position_x", "position_y", "position_z"), geometry["position"])),
                "rotation": geometry["rotation"],
            }
            try:
                self.editor._validate_placement(
                    candidate, [p for key, p in poses.items() if key != item["id"]], dimensions
                )
                poses[item["id"]] = candidate
            except (ConflictError, ValidationError) as error:
                item.update(
                    status="blocked",
                    reason=f"机柜位置不能应用：{error}。v1 不支持有中间碰撞的成组移动/交换位置，请分阶段调整。",
                )
        result["can_apply"] = not any(
            d["status"] in {"blocked", "conflict"} for d in result["diffs"]
        )
        result["preview_token"] = digest([revision.sha256, result["diffs"]])
        return result

    def apply(self, revision_id, preview_token):
        revision = self.history(CadImportRevision, revision_id)
        self.editor._lock_location(revision.location_id)
        receipt = self.receipt(revision.id)
        if receipt:
            return {"applied": True, "receipt": receipt.result}
        # Same Pathway locks as ConnectivityService.create_cable: no claim can race
        # the reference check. Lock every mapped pathway in a stable global order.
        if revision.snapshot_id:
            snapshot = self.history(CadSnapshot, revision.snapshot_id)
            pathway_ids = [
                uuid.UUID(o["attributes"]["pathway_id"])
                for o in snapshot.manifest["objects"]
                if o["kind"] == "pathway_segment"
            ]
            ConnectivityService(self.db, self.editor.principal)._lock_policy_rows(
                Pathway, pathway_ids
            )
        preview = self.preview(revision)
        if not preview["can_apply"] or preview.get("preview_token") != preview_token:
            raise ConflictError(
                "CAD proposal changed or has conflicts; refresh the preview before applying"
            )
        changes = [d for d in preview["diffs"] if d["status"] == "update"]
        for item in changes:
            object_id, geometry = uuid.UUID(item["id"]), item["incoming"]
            if item["kind"] == "rack":
                self.editor.move_rack(
                    object_id,
                    RackPoseUpdate(
                        expected_version=item["current_version"],
                        position_x=geometry["position"][0],
                        position_y=geometry["position"][1],
                        position_z=geometry["position"][2],
                        rotation=geometry["rotation"],
                    ),
                )
            else:
                self.editor._scope(revision.location_id, "pathway:update")
                segment = self.editor._get(PathwaySegment, object_id)
                self.db.refresh(segment)
                if self.references(item["id"]) or segment.version != item["current_version"]:
                    raise ConflictError("Tray geometry or cable references changed")
                room = self.editor._lock_location(revision.location_id)
                dimensions = self.editor._dimensions(room)
                if any(
                    v < 0 or v > limit
                    for p in geometry["points"]
                    for v, limit in zip(p, dimensions)
                ):
                    raise ValidationError("Tray vertices must remain inside the room")
                coordinates = [dict(zip(("x", "y", "z"), p)) for p in geometry["points"]]
                changed = self.db.execute(
                    update(PathwaySegment)
                    .where(
                        PathwaySegment.id == object_id,
                        PathwaySegment.tenant_id == self.editor.principal.tenant_id,
                        PathwaySegment.version == item["current_version"],
                        PathwaySegment.deleted_at.is_(None),
                    )
                    .values(
                        coordinates=coordinates,
                        version=item["current_version"] + 1,
                        updated_at=utcnow(),
                    )
                    .execution_options(synchronize_session=False)
                )
                if changed.rowcount != 1:
                    raise ConflictError("Tray version changed")
                self.audit(
                    "cad.tray_geometry_updated",
                    object_id,
                    {"coordinates": coordinates},
                    before={"coordinates": segment.coordinates},
                )
        result = {
            "revision_id": str(revision.id),
            "sha256": revision.sha256,
            "updated_ids": [d["id"] for d in changes],
        }
        self.db.add(
            CadApplication(
                tenant_id=self.editor.principal.tenant_id, revision_id=revision.id, result=result
            )
        )
        self.audit("cad.applied", revision.id, result)
        self.db.flush()
        return {"applied": True, "receipt": result}

    def audit(self, action, object_id, after, before=None):
        record_audit(
            self.db,
            principal=self.editor.principal,
            action=action,
            object_type="cad_exchange",
            object_id=object_id,
            before=before,
            after=after,
            project_id=self.editor.principal.project_id,
        )

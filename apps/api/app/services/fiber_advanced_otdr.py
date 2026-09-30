"""Advanced Fiber mixin: FiberAdvancedOtdrMixin."""
from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update

from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.fiber_models import (
    ChannelMember,
    ConnectivityChannel,
    CopperPair,
    FiberBreakout,
    FiberBreakoutLeg,
    FiberEndpointClaim,
    FiberPortTermination,
    FiberSplice,
    FiberSpliceEnd,
    OtdrEvent,
    OtdrRecord,
    PhysicalPortClaim,
)
from app.models import Cable, CableTermination, Device, Port, utcnow

class FiberAdvancedOtdrMixin:
    def create_otdr_record(
        self,
        *,
        project_id: uuid.UUID,
        cable_id: uuid.UUID,
        strand_id: uuid.UUID | None,
        direction: str,
        wavelength_nm: int,
        acquired_at: datetime,
        source_name: str,
        source_object_key: str | None = None,
        total_length_m: float | None = None,
        end_to_end_loss_db: float | None = None,
        metadata: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        cable = self._cable(cable_id)
        if cable.project_id != project_id:
            raise AuthorizationError("OTDR record cable is outside the requested project")
        self._authorize("write", project_id)
        if direction not in {"A", "B"}:
            raise ValidationError("OTDR direction must be A or B")
        if type(wavelength_nm) is not int or not 600 <= wavelength_nm <= 1700:
            raise ValidationError("OTDR wavelength must be between 600 and 1700 nm")
        source_name = self._text(source_name, name="OTDR source name", max_length=500)
        source_object_key = self._text(
            source_object_key, name="OTDR source object key", max_length=500, required=False
        )
        if not isinstance(metadata, (dict, type(None))):
            raise ValidationError("OTDR metadata must be an object")
        if strand_id is not None:
            strand, strand_cable = self._strand(strand_id)
            if strand_cable.id != cable.id:
                raise ValidationError("OTDR strand does not belong to the selected cable")
            strand_id = strand.id
        if total_length_m is not None:
            total_length_m = self._finite(total_length_m, name="OTDR length", low=0, high=1_000_000)
        if end_to_end_loss_db is not None:
            end_to_end_loss_db = self._finite(
                end_to_end_loss_db, name="OTDR total loss", low=0, high=100,
            )
        if not isinstance(acquired_at, datetime):
            raise ValidationError("OTDR acquired_at must be a datetime")
        if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
            raise ValidationError("OTDR acquired_at must include a timezone")
        record = OtdrRecord(
            tenant_id=self.tenant_id,
            project_id=project_id,
            cable_id=cable.id,
            strand_id=strand_id,
            direction=direction,
            wavelength_nm=wavelength_nm,
            acquired_at=acquired_at,
            source_name=source_name,
            source_object_key=source_object_key,
            total_length_m=total_length_m,
            end_to_end_loss_db=end_to_end_loss_db,
            metadata_json=metadata or {},
        )
        self.db.add(record)
        self.db.flush()
        event_rows = []
        previous_distance = -1.0
        for sequence, raw in enumerate(events or [], start=1):
            row = self._build_otdr_event(record, sequence, raw)
            if row.distance_m < previous_distance:
                raise ValidationError("OTDR events must be ordered by increasing distance")
            if total_length_m is not None and row.distance_m > total_length_m:
                raise ValidationError("OTDR event distance exceeds the recorded trace length")
            previous_distance = row.distance_m
            event_rows.append(row)
        self.db.add_all(event_rows)
        self.db.flush()
        self._audit("otdr.created", record, project_id, after={
            "cable_id": str(cable.id), "strand_id": str(strand_id) if strand_id else None,
            "wavelength_nm": wavelength_nm, "event_count": len(event_rows),
        })
        return self.get_otdr_record(record.id, action="write")

    def _build_otdr_event(
        self, record: OtdrRecord, sequence: int, raw: dict[str, Any]
    ) -> OtdrEvent:
        if not isinstance(raw, dict):
            raise ValidationError("Each OTDR event must be an object")
        event_type = raw.get("event_type")
        if event_type not in {
            "launch", "connector", "splice", "bend", "reflective", "end", "unknown",
        }:
            raise ValidationError("Unsupported OTDR event type")
        distance = self._finite(
            raw.get("distance_m"), name="OTDR event distance", low=0, high=1_000_000
        )
        loss = raw.get("loss_db")
        reflectance = raw.get("reflectance_db")
        confidence = self._finite(raw.get("confidence", 1.0), name="OTDR confidence", low=0, high=1)
        notes = self._text(
            raw.get("notes"), name="OTDR event notes", max_length=1000, required=False
        )
        return OtdrEvent(
            tenant_id=self.tenant_id,
            record_id=record.id,
            sequence=sequence,
            distance_m=distance,
            event_type=event_type,
            loss_db=(
                None
                if loss is None
                else self._finite(loss, name="OTDR event loss", low=0, high=100)
            ),
            reflectance_db=None if reflectance is None else self._finite(
                reflectance, name="OTDR reflectance", low=-120, high=20,
            ),
            confidence=confidence,
            notes=notes,
        )

    def get_otdr_record(self, record_id: uuid.UUID, *, action: str = "read") -> dict[str, Any]:
        record = self._get(OtdrRecord, record_id)
        self._authorize(action, record.project_id)
        events = self.db.scalars(select(OtdrEvent).where(
            OtdrEvent.tenant_id == self.tenant_id,
            OtdrEvent.record_id == record.id,
            OtdrEvent.deleted_at.is_(None),
        ).order_by(OtdrEvent.sequence).limit(10_000)).all()
        return {
            "id": str(record.id),
            "version": record.version,
            "project_id": str(record.project_id),
            "cable_id": str(record.cable_id),
            "strand_id": str(record.strand_id) if record.strand_id else None,
            "direction": record.direction,
            "wavelength_nm": record.wavelength_nm,
            "acquired_at": record.acquired_at.isoformat(),
            "source_name": record.source_name,
            "source_object_key": record.source_object_key,
            "total_length_m": record.total_length_m,
            "end_to_end_loss_db": record.end_to_end_loss_db,
            "metadata": record.metadata_json,
            "events": [{
                "id": str(event.id),
                "version": event.version,
                "sequence": event.sequence,
                "distance_m": event.distance_m,
                "event_type": event.event_type,
                "loss_db": event.loss_db,
                "reflectance_db": event.reflectance_db,
                "confidence": event.confidence,
                "linked_kind": event.linked_kind,
                "linked_id": str(event.linked_id) if event.linked_id else None,
                "link_offset_m": event.link_offset_m,
                "notes": event.notes,
            } for event in events],
        }

    def link_otdr_event(
        self,
        event_id: uuid.UUID,
        *,
        expected_version: int,
        linked_kind: str,
        linked_id: uuid.UUID,
        link_offset_m: float | None = None,
    ) -> dict[str, Any]:
        event = self._get(OtdrEvent, event_id)
        record = self._get(OtdrRecord, event.record_id)
        self._authorize("write", record.project_id)
        if linked_kind == "splice":
            linked = self._get(FiberSplice, linked_id)
            # Project is verified through the splice slot/cassette path by trace/read.
            self._walk_for_linked_splice(linked, record.project_id)
        elif linked_kind == "fiber_termination":
            linked = self._get(FiberPortTermination, linked_id)
            if linked.project_id != record.project_id:
                raise AuthorizationError("OTDR event link crosses a project boundary")
        elif linked_kind == "breakout_leg":
            linked = self._get(FiberBreakoutLeg, linked_id)
            breakout = self._get(FiberBreakout, linked.breakout_id)
            if breakout.project_id != record.project_id:
                raise AuthorizationError("OTDR event link crosses a project boundary")
        else:
            raise ValidationError("Unsupported OTDR topology link type")
        offset = None if link_offset_m is None else self._finite(
            link_offset_m, name="OTDR link offset", low=-1_000_000, high=1_000_000,
        )
        self._atomic_update(
            OtdrEvent,
            event.id,
            expected_version,
            "OTDR event changed; reload it before retrying",
            linked_kind=linked_kind,
            linked_id=linked.id,
            link_offset_m=offset,
        )
        self._audit("otdr.event_linked", event, record.project_id, after={
            "linked_kind": linked_kind, "linked_id": str(linked.id), "link_offset_m": offset,
        })
        return {
            "id": str(event.id),
            "version": expected_version + 1,
            "linked_kind": linked_kind,
            "linked_id": str(linked.id),
            "link_offset_m": offset,
        }

    def _walk_for_linked_splice(self, splice: FiberSplice, project_id: uuid.UUID) -> None:
        ends = self.db.scalars(select(FiberSpliceEnd).where(
            FiberSpliceEnd.tenant_id == self.tenant_id,
            FiberSpliceEnd.splice_id == splice.id,
            FiberSpliceEnd.deleted_at.is_(None),
        ).limit(3)).all()
        if len(ends) != 2:
            raise ConflictError("Linked splice is incomplete")
        for end in ends:
            _, cable = self._strand(end.strand_id)
            if cable.project_id != project_id:
                raise AuthorizationError("OTDR event link crosses a project boundary")

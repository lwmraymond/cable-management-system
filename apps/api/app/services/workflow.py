from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models import Cable, CableStatus, TestRecord, WorkOrder, WorkOrderStatus
from app.security import Principal, require_permission
from app.services.resource_scope import ResourceScope


class CableWorkflowService:
    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.principal = principal

    def _locked_cable(self, cable_id, permission):
        scope = ResourceScope(self.session, self.principal)
        cable = scope.get(Cable, cable_id)
        scope.require(cable, permission)
        self.session.flush()
        # Serialize testing and approval on the same cable, including SQLite where
        # FOR UPDATE is ignored. The no-op does not advance the domain version.
        if self.session.get_bind().dialect.name == "sqlite":
            self.session.execute(
                update(Cable)
                .where(Cable.id == cable.id, Cable.tenant_id == self.principal.tenant_id)
                .values(version=Cable.version, updated_at=Cable.updated_at)
                .execution_options(synchronize_session=False)
            )
        locked = self.session.scalar(
            select(Cable)
            .where(
                Cable.id == cable.id,
                Cable.tenant_id == self.principal.tenant_id,
                Cable.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None:
            raise NotFoundError("Cable not found in tenant")
        # Everything pending was flushed above. Refresh cached identities/grants as
        # well as the cable because authorization may change while waiting for the lock.
        self.session.expire_all()
        ResourceScope(self.session, self.principal).require(locked, permission)
        return locked

    def _work_order(self, work_order_id, cable, permission):
        if work_order_id is None:
            return None
        scope = ResourceScope(self.session, self.principal)
        work_order = scope.get(WorkOrder, work_order_id)
        if work_order.cable_id not in {None, cable.id} or work_order.project_id != cable.project_id:
            raise NotFoundError("Work order not found for cable in the same project")
        scope.require(work_order, permission)
        return work_order

    def mark_installed(self, cable_id: uuid.UUID, work_order_id: uuid.UUID | None = None) -> Cable:
        require_permission(self.principal, "cable:install")
        cable = self._locked_cable(cable_id, "cable:install")
        work_order = self._work_order(work_order_id, cable, "cable:install")
        allowed = {
            CableStatus.PLANNED,
            CableStatus.APPROVED,
            CableStatus.ORDERED,
            CableStatus.STAGED,
            CableStatus.TERMINATED,
        }
        if cable.installation_status not in allowed:
            raise ConflictError(f"Cable cannot be installed from {cable.installation_status.value}")
        before = cable.installation_status.value
        cable.installation_status = CableStatus.INSTALLED
        cable.installer_id = self.principal.actor_id
        cable.installed_at = datetime.now(UTC)
        if work_order:
            work_order.status = WorkOrderStatus.AWAITING_TEST
        record_audit(
            self.session,
            principal=self.principal,
            action="cable.installed",
            object_type="cable",
            object_id=cable.id,
            before={"status": before},
            after={"status": cable.installation_status.value},
            project_id=cable.project_id,
        )
        return cable

    def submit_test(
        self,
        *,
        cable_id: uuid.UUID,
        result: str,
        measurements: dict[str, Any],
        work_order_id: uuid.UUID | None = None,
        attachment_name: str | None = None,
    ) -> TestRecord:
        require_permission(self.principal, "cable:test")
        cable = self._locked_cable(cable_id, "cable:test")
        work_order = self._work_order(work_order_id, cable, "cable:test")
        if cable.installation_status not in {
            CableStatus.INSTALLED,
            CableStatus.TERMINATED,
            CableStatus.TESTED,
        }:
            raise ConflictError("Cable must be installed or terminated before testing")
        normalized = result.upper()
        if normalized not in {"PASS", "FAIL"}:
            raise ValidationError("Test result must be PASS or FAIL")
        record = TestRecord(
            tenant_id=self.principal.tenant_id,
            cable_id=cable.id,
            work_order_id=work_order_id,
            tester_id=self.principal.actor_id,
            result=normalized,
            measurements=measurements,
            tested_at=datetime.now(UTC),
            attachment_name=attachment_name,
        )
        self.session.add(record)
        self.session.flush()
        cable.test_status = normalized
        cable.tested_at = record.tested_at
        cable.installation_status = CableStatus.TESTED
        if work_order:
            work_order.status = WorkOrderStatus.AWAITING_APPROVAL
        record_audit(
            self.session,
            principal=self.principal,
            action="cable.tested",
            object_type="test_record",
            object_id=record.id,
            after={"cable_id": str(cable.id), "result": normalized},
            project_id=cable.project_id,
        )
        return record

    def approve_test(self, test_record_id: uuid.UUID) -> TestRecord:
        require_permission(self.principal, "cable:approve")
        scope = ResourceScope(self.session, self.principal)
        record = scope.get(TestRecord, test_record_id)
        cable = self._locked_cable(record.cable_id, "cable:approve")
        self.session.refresh(record)
        if record.tester_id == self.principal.actor_id:
            raise ConflictError("A tester may not approve their own restricted test record")
        if record.result != "PASS":
            raise ConflictError("Failed test records cannot commission a cable")
        latest_id = self.session.scalar(
            select(TestRecord.id)
            .where(
                TestRecord.tenant_id == self.principal.tenant_id,
                TestRecord.cable_id == cable.id,
                TestRecord.deleted_at.is_(None),
            )
            .order_by(
                TestRecord.tested_at.desc(), TestRecord.created_at.desc(), TestRecord.id.desc()
            )
            .limit(1)
        )
        if latest_id != record.id or cable.test_status != "PASS":
            raise ConflictError("Test was superseded; only the latest passing test can be approved")
        if record.status == "approved" and cable.installation_status == CableStatus.IN_SERVICE:
            return record
        if cable.installation_status != CableStatus.TESTED:
            raise ConflictError("Cable must be in tested state before approval")
        work_order = self._work_order(record.work_order_id, cable, "cable:approve")
        record.status = "approved"
        record.approved_by = self.principal.actor_id
        record.approved_at = datetime.now(UTC)
        before = cable.installation_status.value
        cable.installation_status = CableStatus.IN_SERVICE
        if work_order:
            work_order.status = WorkOrderStatus.COMPLETED
        record_audit(
            self.session,
            principal=self.principal,
            action="cable.commissioned",
            object_type="cable",
            object_id=cable.id,
            before={"status": before},
            after={"status": cable.installation_status.value, "test_record": str(record.id)},
            project_id=cable.project_id,
        )
        return record

"""Apply grant scope to actual legacy inventory objects, not caller-selected headers."""

from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.exceptions import AuthorizationError, NotFoundError
from app.models import (
    Cable,
    CableRouteSegment,
    CableTermination,
    Device,
    Location,
    Pathway,
    PathwaySegment,
    Port,
    Project,
    Rack,
    TestRecord,
    WorkOrder,
)
from app.security import Principal, require_permission, resolve_principal


class ResourceScope:
    def __init__(self, db: Session, principal: Principal):
        self.db = db
        # Refresh membership/grant validity even when a service is handed a cached principal.
        self.principal = resolve_principal(
            db,
            actor_id=principal.actor_id,
            tenant_id=principal.tenant_id,
            project_id=principal.project_id,
            location_id=principal.location_id,
            request_id=principal.request_id,
            ip_address=principal.ip_address,
            user_agent=principal.user_agent,
        )
        self._decisions: dict[tuple, bool] = {}

    def get(self, model, object_id):
        row = self.db.scalar(
            select(model)
            .where(
                model.id == object_id,
                model.tenant_id == self.principal.tenant_id,
                model.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError(f"{model.__name__} not found in tenant")
        return row

    def _location(self, permission, project_id, location_id):
        require_permission(self.principal, permission)
        if self.principal.is_tenant_member:
            return
        # Unassigned inventory cannot be attributed to a project-scoped grant.
        if project_id is None:
            raise AuthorizationError("Resource has no authorized project scope")
        key = (permission, project_id, location_id)
        if key not in self._decisions:
            self.get(Project, project_id)
            if location_id is not None:
                self.get(Location, location_id)
            try:
                actual = resolve_principal(
                    self.db,
                    actor_id=self.principal.actor_id,
                    tenant_id=self.principal.tenant_id,
                    project_id=project_id,
                    location_id=location_id,
                )
                require_permission(actual, permission)
                self._decisions[key] = True
            except AuthorizationError:
                self._decisions[key] = False
        if not self._decisions[key]:
            raise AuthorizationError("Resource is outside the authorized project/location scope")

    def require_location(self, permission, project_id, location_id):
        """Authorize a new resource's actual references before it has a row/id."""
        require_permission(self.principal, permission)
        if project_id is not None:
            self.get(Project, project_id)
        if location_id is not None:
            self.get(Location, location_id)
        self._location(permission, project_id, location_id)

    def require(self, row, permission):
        if row.tenant_id != self.principal.tenant_id or row.deleted_at is not None:
            raise NotFoundError("Resource not found in tenant")
        require_permission(self.principal, permission)
        if self.principal.is_tenant_member:
            return
        if isinstance(row, Cable):
            locations = self.db.scalars(
                select(Device.location_id)
                .select_from(CableTermination)
                .outerjoin(
                    Port,
                    and_(
                        Port.id == CableTermination.port_id,
                        Port.tenant_id == row.tenant_id,
                        Port.deleted_at.is_(None),
                    ),
                )
                .outerjoin(
                    Device,
                    and_(
                        Device.id == Port.device_id,
                        Device.tenant_id == row.tenant_id,
                        Device.deleted_at.is_(None),
                    ),
                )
                .where(
                    CableTermination.cable_id == row.id,
                    CableTermination.tenant_id == row.tenant_id,
                    CableTermination.deleted_at.is_(None),
                )
            ).all()
            # Unknown endpoints require a project-wide grant, never a room-only grant.
            if len(locations) < 2:
                locations.append(None)
            locations.extend(
                self.db.scalars(
                    select(Pathway.location_id)
                    .select_from(CableRouteSegment)
                    .outerjoin(
                        PathwaySegment,
                        and_(
                            PathwaySegment.id == CableRouteSegment.pathway_segment_id,
                            PathwaySegment.tenant_id == row.tenant_id,
                            PathwaySegment.deleted_at.is_(None),
                        ),
                    )
                    .outerjoin(
                        Pathway,
                        and_(
                            Pathway.id == PathwaySegment.pathway_id,
                            Pathway.tenant_id == row.tenant_id,
                            Pathway.deleted_at.is_(None),
                        ),
                    )
                    .where(
                        CableRouteSegment.cable_id == row.id,
                        CableRouteSegment.tenant_id == row.tenant_id,
                        CableRouteSegment.deleted_at.is_(None),
                    )
                ).all()
            )
            for location in set(locations):
                self._location(permission, row.project_id, location)
        elif isinstance(row, WorkOrder):
            if row.cable_id:
                cable = self.get(Cable, row.cable_id)
                if cable.project_id != row.project_id:
                    raise AuthorizationError("Work order and cable projects do not match")
                self.require(cable, permission)
            if row.location_id is not None or row.cable_id is None:
                self._location(permission, row.project_id, row.location_id)
        elif isinstance(row, TestRecord):
            self.require(self.get(Cable, row.cable_id), permission)
        elif isinstance(row, Port):
            self.require(self.get(Device, row.device_id), permission)
        elif isinstance(row, Location):
            self._location(permission, self.principal.project_id, row.id)
        elif isinstance(row, (Rack, Device, Pathway)):
            self._location(permission, self.principal.project_id, row.location_id)
        else:
            raise AuthorizationError("Resource scope cannot be established")

    def iter_visible(self, rows, permission):
        for row in rows:
            try:
                self.require(row, permission)
            except (AuthorizationError, NotFoundError):
                continue
            yield row

    def visible(self, rows, permission):
        return list(self.iter_visible(rows, permission))

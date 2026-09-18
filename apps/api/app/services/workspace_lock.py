"""Serialize workspace sharing changes and legacy access-grant mutations."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Tenant


def lock_workspace(db: Session, workspace_id: uuid.UUID) -> Tenant | None:
    """Hold the workspace ACL lock until commit/rollback and refresh its state."""
    # SQLite ignores FOR UPDATE; acquire its write lock before reading ACL state.
    if db.get_bind().dialect.name == "sqlite":
        db.execute(
            update(Tenant)
            .where(Tenant.id == workspace_id, Tenant.active.is_(True))
            .values(updated_at=Tenant.updated_at)
            .execution_options(synchronize_session=False)
        )
    return db.scalar(
        select(Tenant)
        .where(Tenant.id == workspace_id, Tenant.active.is_(True))
        .with_for_update()
        .execution_options(populate_existing=True)
    )

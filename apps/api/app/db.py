from __future__ import annotations

import uuid
from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import Engine, and_, create_engine, event, or_, text
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria

from app import fiber_models  # noqa: F401 -- register extension metadata
from app import floorplan_models  # noqa: F401 -- register Floor Plan metadata
from app.config import get_settings
from app.models import AuditEvent, Base, StandardProfile, TenantOwnedMixin

settings = get_settings()


def _connect_args(url: str) -> dict[str, object]:
    return {"check_same_thread": False} if url.startswith("sqlite") else {}


engine = create_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args(settings.database_url),
)
platform_database_url = settings.platform_database_url or settings.database_url
platform_engine = create_engine(
    platform_database_url,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args(platform_database_url),
)
SessionLocal = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)
PlatformSessionLocal = sessionmaker(
    bind=platform_engine, class_=Session, autoflush=False, expire_on_commit=False
)


@event.listens_for(Session, "do_orm_execute")
def _tenant_filter(execute_state) -> None:  # type: ignore[no-untyped-def]
    if not execute_state.is_select or execute_state.execution_options.get("skip_tenant_criteria"):
        return
    tenant_id = execute_state.session.info.get("tenant_id")
    bypass = execute_state.session.info.get("bypass_tenant")
    if not tenant_id or bypass:
        return
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            TenantOwnedMixin,
            lambda model: and_(model.tenant_id == tenant_id, model.deleted_at.is_(None)),
            include_aliases=True,
        ),
        with_loader_criteria(
            AuditEvent,
            lambda model: model.tenant_id == tenant_id,
            include_aliases=True,
        ),
        with_loader_criteria(
            StandardProfile,
            lambda model: or_(model.tenant_id.is_(None), model.tenant_id == tenant_id),
            include_aliases=True,
        ),
    )


@event.listens_for(Session, "before_flush")
def _tenant_write_guard(session: Session, _flush_context, _instances) -> None:  # type: ignore[no-untyped-def]
    tenant_id: uuid.UUID | None = session.info.get("tenant_id")
    bypass = session.info.get("bypass_tenant")
    if tenant_id and not bypass:
        for obj in session.new.union(session.dirty).union(session.deleted):
            object_tenant = getattr(obj, "tenant_id", None)
            if object_tenant is not None and object_tenant != tenant_id:
                raise PermissionError("Cross-tenant write rejected")
    if not session.info.get("allow_audit_mutation"):
        for obj in session.dirty.union(session.deleted):
            if isinstance(obj, AuditEvent):
                raise PermissionError("Audit events are immutable")


@event.listens_for(Session, "before_flush")
def _increment_versions(session: Session, _flush_context, _instances) -> None:  # type: ignore[no-untyped-def]
    for obj in session.dirty:
        if isinstance(obj, TenantOwnedMixin) and session.is_modified(obj, include_collections=False):
            obj.version += 1


def set_postgres_tenant_context(session: Session, tenant_id: uuid.UUID) -> None:
    session.info["tenant_id"] = tenant_id
    if session.bind and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )


def set_platform_bypass(session: Session, enabled: bool = True) -> None:
    session.info["bypass_tenant"] = enabled


def create_all(target: Engine | None = None) -> None:
    Base.metadata.create_all(target or engine)


@contextmanager
def session_scope(
    *, tenant_id: uuid.UUID | None = None, bypass: bool = False
) -> Iterator[Session]:
    factory = PlatformSessionLocal if bypass else SessionLocal
    session = factory()
    try:
        if bypass:
            set_platform_bypass(session)
        elif tenant_id:
            set_postgres_tenant_context(session, tenant_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

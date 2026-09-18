from __future__ import annotations

import uuid
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.auth import extract_bearer_token, resolve_oidc_actor, verify_oidc_token
from app.config import get_settings
from app.db import (
    PlatformSessionLocal,
    SessionLocal,
    set_platform_bypass,
    set_postgres_tenant_context,
)
from app.exceptions import AuthenticationError
from app.models import UserIdentity
from app.security import Principal, resolve_principal


def get_platform_db() -> Generator[Session, None, None]:
    session = PlatformSessionLocal()
    set_platform_bypass(session)
    try:
        yield session
    finally:
        session.close()


def get_db(
    x_tenant_id: uuid.UUID = Header(alias="X-Tenant-ID"),
) -> Generator[Session, None, None]:
    session = SessionLocal()
    set_postgres_tenant_context(session, x_tenant_id)
    try:
        yield session
    finally:
        session.close()


def authenticate_actor(
    request: Request,
    db: Session,
    *,
    tenant_id: uuid.UUID,
    x_actor_id: uuid.UUID | None,
) -> tuple[uuid.UUID, dict[str, Any] | None]:
    settings = get_settings()
    bearer_token = extract_bearer_token(request.headers.get("authorization"))
    cookie_token = (
        request.cookies.get(settings.auth_cookie_name) if settings.cookie_auth_enabled else None
    )
    if bearer_token and cookie_token and bearer_token != cookie_token:
        raise AuthenticationError("Conflicting bearer and cookie credentials")
    token = bearer_token or cookie_token

    if token:
        if settings.auth_mode == "demo":
            raise AuthenticationError("OIDC credentials are disabled in demo authentication mode")
        claims = verify_oidc_token(token, settings)
        authentication = resolve_oidc_actor(
            db,
            claims,
            requested_tenant_id=str(tenant_id),
            settings=settings,
        )
        request.state.auth_method = "bearer" if bearer_token else "cookie"
        request.state.oidc_claims = claims
        return authentication.actor.id, claims

    if settings.auth_mode == "oidc":
        raise AuthenticationError("Bearer authentication is required")
    if not settings.demo_mode:
        raise AuthenticationError("Development header authentication is disabled")
    if x_actor_id is None:
        raise AuthenticationError("X-Actor-ID is required for development authentication")
    request.state.auth_method = "demo"
    request.state.oidc_claims = None
    return x_actor_id, None


@dataclass(frozen=True)
class AuthenticatedActor:
    actor: UserIdentity
    claims: dict[str, Any] | None
    auth_method: Literal["bearer", "cookie", "demo"]


def get_authenticated_actor(
    request: Request,
    db: Session = Depends(get_platform_db),
    x_actor_id: uuid.UUID | None = Header(default=None, alias="X-Actor-ID"),
) -> AuthenticatedActor:
    """Account bootstrap only; callers must separately enforce tenant/workspace access."""
    settings = get_settings()
    bearer = extract_bearer_token(request.headers.get("authorization"))
    cookie = (
        request.cookies.get(settings.auth_cookie_name) if settings.cookie_auth_enabled else None
    )
    if bearer and cookie and bearer != cookie:
        raise AuthenticationError("Conflicting bearer and cookie credentials")
    token = bearer or cookie
    if token:
        if settings.auth_mode == "demo":
            raise AuthenticationError("OIDC credentials are disabled in demo authentication mode")
        try:
            claims = verify_oidc_token(token, settings)
        except (TypeError, ValueError, OverflowError) as exc:
            raise AuthenticationError("OIDC token claims are malformed") from exc
        # This dependency establishes identity, not tenant authority. Passing the
        # verified claim keeps the existing resolver's account-linking behavior;
        # workspace callers must check this claim against every selected tenant.
        actor = resolve_oidc_actor(
            db,
            claims,
            requested_tenant_id=str(claims.get(settings.oidc_tenant_claim, "")),
            settings=settings,
        ).actor
        method = "bearer" if bearer else "cookie"
        request.state.auth_method = method
        request.state.oidc_claims = claims
        return AuthenticatedActor(actor, claims, method)
    if settings.auth_mode == "oidc" or not settings.demo_mode:
        raise AuthenticationError("Authenticated browser session or bearer token is required")
    if x_actor_id is None:
        raise AuthenticationError("X-Actor-ID is required for development authentication")
    actor = db.get(UserIdentity, x_actor_id)
    if actor is None or not actor.active:
        raise AuthenticationError("Unknown or inactive identity")
    request.state.auth_method = "demo"
    request.state.oidc_claims = None
    return AuthenticatedActor(actor, None, "demo")


def get_principal(
    request: Request,
    db: Session = Depends(get_db),
    x_tenant_id: uuid.UUID = Header(alias="X-Tenant-ID"),
    x_actor_id: uuid.UUID | None = Header(default=None, alias="X-Actor-ID"),
    x_project_id: uuid.UUID | None = Header(default=None, alias="X-Project-ID"),
    x_location_id: uuid.UUID | None = Header(default=None, alias="X-Location-ID"),
) -> Principal:
    actor_id, _claims = authenticate_actor(
        request,
        db,
        tenant_id=x_tenant_id,
        x_actor_id=x_actor_id,
    )
    return resolve_principal(
        db,
        actor_id=actor_id,
        tenant_id=x_tenant_id,
        project_id=x_project_id,
        location_id=x_location_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

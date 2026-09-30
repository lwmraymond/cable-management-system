"""Strict NetBox synchronization and durable webhook API."""
from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.integration_models import WebhookEndpoint
from app.security import Principal
from app.services.netbox_adapter import NetBoxClient, NetBoxSyncService
from app.services.signed_webhooks import SignedWebhookService


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NetBoxSyncRequest(StrictPayload):
    name: str = Field(min_length=1, max_length=180)
    base_url: HttpUrl
    token: SecretStr
    resource: Literal[
        "dcim/devices",
        "dcim/interfaces",
        "dcim/cables",
        "dcim/racks",
        "dcim/locations",
    ]
    project_id: uuid.UUID
    location_id: uuid.UUID
    updated_after: str | None = Field(default=None, max_length=1000)
    allow_http: bool = False


class WebhookEndpointCreate(StrictPayload):
    name: str = Field(min_length=1, max_length=180)
    url: HttpUrl
    secret_reference: str = Field(min_length=1, max_length=500)
    timeout_seconds: float = Field(default=10.0, ge=1, le=60, allow_inf_nan=False, strict=True)
    max_attempts: int = Field(default=8, ge=1, le=20, strict=True)
    allow_http: bool = False


class WebhookEventCreate(StrictPayload):
    endpoint_id: uuid.UUID
    event_type: str = Field(min_length=1, max_length=180)
    payload: dict[str, Any]
    event_id: uuid.UUID | None = None


def commit_operation(db: Session, operation: Callable, **kwargs):
    try:
        result = operation(**kwargs)
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        raise ConflictError("Integration identity, event or endpoint already exists") from None
    except OperationalError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Database is temporarily unavailable",
            headers={"Retry-After": "1"},
        ) from None
    except Exception:
        db.rollback()
        raise


def environment_secret(reference: str) -> str:
    if not reference.startswith("env:"):
        raise ValidationError("Only env: secret references are supported by the built-in dispatcher")
    name = reference[4:]
    if not name or not name.replace("_", "").isalnum() or name.upper() != name:
        raise ValidationError("Invalid environment secret reference")
    value = os.getenv(name)
    if not value:
        raise ValidationError("Webhook secret is unavailable")
    return value


def build_integrations_router(
    get_db: Callable,
    get_principal: Callable,
    *,
    secret_resolver: Callable[[str], str] = environment_secret,
    http_client_factory: Callable[[], httpx.Client] = httpx.Client,
) -> APIRouter:
    router = APIRouter(prefix="/integrations", tags=["integrations"])

    @router.post("/netbox/sync")
    def sync_netbox(
        body: NetBoxSyncRequest,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        client = NetBoxClient(
            str(body.base_url),
            body.token.get_secret_value(),
            allow_http=body.allow_http,
        )
        try:
            return commit_operation(
                db,
                NetBoxSyncService(db, principal).sync,
                name=body.name,
                resource=body.resource,
                base_url=str(body.base_url),
                project_id=body.project_id,
                location_id=body.location_id,
                client=client,
                updated_after=body.updated_after,
            )
        finally:
            client.close()

    @router.post("/webhooks/endpoints", status_code=201)
    def create_webhook_endpoint(
        body: WebhookEndpointCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            SignedWebhookService(db, principal).create_endpoint,
            name=body.name,
            url=str(body.url),
            secret_reference=body.secret_reference,
            timeout_seconds=body.timeout_seconds,
            max_attempts=body.max_attempts,
            allow_http=body.allow_http,
        )

    @router.post("/webhooks/events", status_code=202)
    def enqueue_webhook(
        body: WebhookEventCreate,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            SignedWebhookService(db, principal).enqueue,
            **body.model_dump(),
        )

    @router.get("/webhooks/outbox/{outbox_id}")
    def get_outbox(
        outbox_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return SignedWebhookService(db, principal).describe(outbox_id)

    @router.post("/webhooks/outbox/{outbox_id}/dispatch")
    def dispatch_outbox(
        outbox_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        client = http_client_factory()
        try:
            return commit_operation(
                db,
                SignedWebhookService(db, principal).dispatch_one,
                outbox_id=outbox_id,
                secret_resolver=secret_resolver,
                client=client,
            )
        finally:
            client.close()

    @router.post("/webhooks/outbox/{outbox_id}/requeue")
    def requeue_outbox(
        outbox_id: uuid.UUID,
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        return commit_operation(
            db,
            SignedWebhookService(db, principal).requeue_dead,
            outbox_id=outbox_id,
        )

    @router.post("/webhooks/inbound/{endpoint_name}")
    async def verify_inbound(
        endpoint_name: str,
        request: Request,
        x_sim_event_id: str = Header(alias="X-SIM-Event-ID"),
        x_sim_timestamp: str = Header(alias="X-SIM-Timestamp"),
        x_sim_signature: str = Header(alias="X-SIM-Signature"),
        db: Session = Depends(get_db),
        principal: Principal = Depends(get_principal),
    ):
        endpoint = db.scalar(
            select(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == principal.tenant_id,
                WebhookEndpoint.name == endpoint_name,
                WebhookEndpoint.active.is_(True),
                WebhookEndpoint.deleted_at.is_(None),
            )
        )
        if endpoint is None:
            raise NotFoundError("Inbound webhook endpoint not found")
        payload = SignedWebhookService(db, principal).verify_inbound(
            endpoint_name=endpoint_name,
            body=await request.body(),
            event_id=x_sim_event_id,
            timestamp=x_sim_timestamp,
            signature=x_sim_signature,
            secret=secret_resolver(endpoint.secret_reference),
        )
        db.commit()
        return {"accepted": True, "event_id": x_sim_event_id, "payload": payload}

    return router

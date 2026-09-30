"""Durable signed webhook outbox, delivery retry and inbound replay defense."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.integration_models import (
    WebhookDeliveryAttempt,
    WebhookEndpoint,
    WebhookInboundReceipt,
    WebhookOutbox,
)
from app.models import Tenant
from app.security import Principal, require_permission, resolve_principal


class SignedWebhookService:
    MAX_PAYLOAD_BYTES = 512 * 1024
    MAX_RESPONSE_EXCERPT = 2_000

    def __init__(self, session: Session, principal: Principal):
        self.db = session
        self.principal = principal
        self.tenant_id = principal.tenant_id
        if session.info.get("bypass_tenant"):
            raise AuthorizationError("Webhook operations cannot use a platform bypass session")
        if session.info.get("tenant_id") != self.tenant_id:
            raise AuthorizationError("Webhook operations require a matching tenant session")

    def _fresh(self, permission: str):
        tenant = self.db.scalar(
            select(Tenant).where(Tenant.id == self.tenant_id, Tenant.active.is_(True))
        )
        if tenant is None:
            raise AuthorizationError("Tenant is inactive or unavailable")
        actual = resolve_principal(
            self.db,
            actor_id=self.principal.actor_id,
            tenant_id=self.tenant_id,
            request_id=self.principal.request_id,
            ip_address=self.principal.ip_address,
            user_agent=self.principal.user_agent,
        )
        require_permission(actual, permission)
        return actual

    def _get(self, model, object_id: uuid.UUID):
        row = self.db.scalar(
            select(model).where(
                model.id == object_id,
                model.tenant_id == self.tenant_id,
                model.deleted_at.is_(None),
            )
        )
        if row is None:
            raise NotFoundError("Webhook resource not found")
        return row

    @staticmethod
    def _canonical(payload: dict[str, Any]) -> bytes:
        if not isinstance(payload, dict):
            raise ValidationError("Webhook payload must be an object")
        try:
            body = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValidationError("Webhook payload is not canonical JSON") from exc
        if len(body) > SignedWebhookService.MAX_PAYLOAD_BYTES:
            raise ValidationError("Webhook payload exceeds the safety limit")
        return body

    @staticmethod
    def _url(value: str, *, allow_http: bool = False) -> str:
        parsed = urlparse(value)
        schemes = {"https", "http"} if allow_http else {"https"}
        if parsed.scheme not in schemes or not parsed.netloc:
            raise ValidationError("Webhook URL must use HTTPS")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValidationError("Webhook URL cannot contain credentials or fragments")
        return parsed.geturl()

    @staticmethod
    def signature(secret: str, timestamp: int, event_id: uuid.UUID, body: bytes) -> str:
        if not isinstance(secret, str) or len(secret.encode("utf-8")) < 16:
            raise ValidationError("Webhook signing secret must contain at least 16 bytes")
        material = str(timestamp).encode("ascii") + b"." + str(event_id).encode("ascii") + b"." + body
        digest = hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()
        return f"v1={digest}"

    def create_endpoint(
        self,
        *,
        name: str,
        url: str,
        secret_reference: str,
        timeout_seconds: float = 10.0,
        max_attempts: int = 8,
        allow_http: bool = False,
    ):
        actual = self._fresh("webhook:manage")
        clean_name = name.strip() if isinstance(name, str) else ""
        clean_reference = secret_reference.strip() if isinstance(secret_reference, str) else ""
        if not clean_name or len(clean_name) > 180:
            raise ValidationError("Webhook endpoint name must contain 1 to 180 characters")
        if not clean_reference or len(clean_reference) > 500:
            raise ValidationError("Webhook secret reference must contain 1 to 500 characters")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValidationError("Webhook timeout must be numeric")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or not 1 <= timeout <= 60:
            raise ValidationError("Webhook timeout must be between 1 and 60 seconds")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 20:
            raise ValidationError("Webhook max_attempts must be between 1 and 20")
        endpoint = WebhookEndpoint(
            tenant_id=self.tenant_id,
            name=clean_name,
            url=self._url(url, allow_http=allow_http),
            secret_reference=clean_reference,
            active=True,
            timeout_seconds=timeout,
            max_attempts=max_attempts,
        )
        self.db.add(endpoint); self.db.flush()
        record_audit(
            self.db,
            principal=actual,
            action="webhook.endpoint.created",
            object_type="webhook_endpoint",
            object_id=endpoint.id,
            after={"name": clean_name, "url": endpoint.url, "secret_reference": clean_reference},
        )
        return {
            "id": str(endpoint.id),
            "name": endpoint.name,
            "url": endpoint.url,
            "active": endpoint.active,
            "max_attempts": endpoint.max_attempts,
        }

    def enqueue(
        self,
        *,
        endpoint_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        event_id: uuid.UUID | None = None,
    ):
        actual = self._fresh("webhook:send")
        endpoint = self._get(WebhookEndpoint, endpoint_id)
        if not endpoint.active:
            raise ConflictError("Webhook endpoint is inactive")
        clean_type = event_type.strip() if isinstance(event_type, str) else ""
        if not clean_type or len(clean_type) > 180:
            raise ValidationError("Webhook event type must contain 1 to 180 characters")
        body = self._canonical(payload)
        now = datetime.now(UTC)
        outbox = WebhookOutbox(
            tenant_id=self.tenant_id,
            endpoint_id=endpoint.id,
            event_id=event_id or uuid.uuid4(),
            event_type=clean_type,
            payload=payload,
            payload_sha256=hashlib.sha256(body).hexdigest(),
            state="pending",
            attempts=0,
            next_attempt_at=now,
        )
        self.db.add(outbox); self.db.flush()
        record_audit(
            self.db,
            principal=actual,
            action="webhook.event.enqueued",
            object_type="webhook_outbox",
            object_id=outbox.id,
            after={
                "event_id": str(outbox.event_id),
                "event_type": clean_type,
                "endpoint_id": str(endpoint.id),
                "payload_sha256": outbox.payload_sha256,
            },
        )
        return self.describe(outbox.id)

    def describe(self, outbox_id: uuid.UUID):
        self._fresh("webhook:read")
        row = self._get(WebhookOutbox, outbox_id)
        attempts = self.db.scalars(
            select(WebhookDeliveryAttempt)
            .where(
                WebhookDeliveryAttempt.tenant_id == self.tenant_id,
                WebhookDeliveryAttempt.outbox_id == row.id,
                WebhookDeliveryAttempt.deleted_at.is_(None),
            )
            .order_by(WebhookDeliveryAttempt.attempt_number)
        ).all()
        return {
            "id": str(row.id),
            "event_id": str(row.event_id),
            "event_type": row.event_type,
            "endpoint_id": str(row.endpoint_id),
            "state": row.state,
            "attempts": row.attempts,
            "next_attempt_at": row.next_attempt_at.isoformat(),
            "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
            "last_error": row.last_error,
            "payload_sha256": row.payload_sha256,
            "delivery_attempts": [
                {
                    "attempt": item.attempt_number,
                    "timestamp": item.signature_timestamp,
                    "status_code": item.status_code,
                    "duration_ms": item.duration_ms,
                    "error": item.error,
                }
                for item in attempts
            ],
        }

    @staticmethod
    def _backoff(event_id: uuid.UUID, attempt: int) -> float:
        base = min(3600.0, 5.0 * (2 ** min(attempt - 1, 9)))
        seed = hashlib.sha256(f"{event_id}:{attempt}".encode("ascii")).digest()
        jitter = int.from_bytes(seed[:2], "big") / 65535 * base * 0.25
        return base + jitter

    def dispatch_one(
        self,
        outbox_id: uuid.UUID,
        *,
        secret_resolver: Callable[[str], str],
        client: httpx.Client | None = None,
        now: datetime | None = None,
    ):
        self._fresh("webhook:dispatch")
        row = self._get(WebhookOutbox, outbox_id)
        endpoint = self._get(WebhookEndpoint, row.endpoint_id)
        current = now or datetime.now(UTC)
        if row.state == "delivered":
            return self.describe(row.id)
        if row.state == "dead":
            raise ConflictError("Dead-letter webhook requires explicit requeue")
        if row.next_attempt_at > current:
            raise ConflictError("Webhook is not due yet")
        if not endpoint.active:
            raise ConflictError("Webhook endpoint is inactive")
        secret = secret_resolver(endpoint.secret_reference)
        body = self._canonical(row.payload)
        if hashlib.sha256(body).hexdigest() != row.payload_sha256:
            row.state = "dead"
            row.last_error = "Stored webhook payload checksum mismatch"
            self.db.flush()
            raise ConflictError(row.last_error)

        attempt_number = row.attempts + 1
        timestamp = int(current.timestamp())
        signature = self.signature(secret, timestamp, row.event_id, body)
        row.state = "sending"
        row.attempts = attempt_number
        self.db.flush()
        http = client or httpx.Client(timeout=endpoint.timeout_seconds)
        owns_client = client is None
        started = time.perf_counter()
        status_code = None
        response_excerpt = None
        error_text = None
        try:
            response = http.post(
                endpoint.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "structured-infrastructure-manager/0.1 webhook",
                    "X-SIM-Event-ID": str(row.event_id),
                    "X-SIM-Event-Type": row.event_type,
                    "X-SIM-Timestamp": str(timestamp),
                    "X-SIM-Signature": signature,
                },
                timeout=endpoint.timeout_seconds,
            )
            status_code = response.status_code
            response_excerpt = response.text[: self.MAX_RESPONSE_EXCERPT]
            if 200 <= response.status_code < 300:
                row.state = "delivered"
                row.delivered_at = current
                row.last_error = None
            else:
                error_text = f"HTTP {response.status_code}"
                if attempt_number >= endpoint.max_attempts:
                    row.state = "dead"
                else:
                    row.state = "retry"
                    row.next_attempt_at = current + timedelta(
                        seconds=self._backoff(row.event_id, attempt_number)
                    )
                row.last_error = error_text
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"[:1000]
            if attempt_number >= endpoint.max_attempts:
                row.state = "dead"
            else:
                row.state = "retry"
                row.next_attempt_at = current + timedelta(
                    seconds=self._backoff(row.event_id, attempt_number)
                )
            row.last_error = error_text
        finally:
            if owns_client:
                http.close()
        duration_ms = (time.perf_counter() - started) * 1000
        self.db.add(
            WebhookDeliveryAttempt(
                tenant_id=self.tenant_id,
                outbox_id=row.id,
                attempt_number=attempt_number,
                signature_timestamp=timestamp,
                status_code=status_code,
                duration_ms=duration_ms,
                response_excerpt=response_excerpt,
                error=error_text,
            )
        )
        self.db.flush()
        return self.describe(row.id)

    def requeue_dead(self, outbox_id: uuid.UUID):
        actual = self._fresh("webhook:manage")
        row = self._get(WebhookOutbox, outbox_id)
        if row.state != "dead":
            raise ConflictError("Only dead-letter events can be manually requeued")
        row.state = "retry"
        row.next_attempt_at = datetime.now(UTC)
        row.last_error = None
        self.db.flush()
        record_audit(
            self.db,
            principal=actual,
            action="webhook.event.requeued",
            object_type="webhook_outbox",
            object_id=row.id,
            after={"event_id": str(row.event_id), "attempts": row.attempts},
        )
        return self.describe(row.id)

    def verify_inbound(
        self,
        *,
        endpoint_name: str,
        body: bytes,
        event_id: str,
        timestamp: str,
        signature: str,
        secret: str,
        tolerance_seconds: int = 300,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        self._fresh("webhook:receive")
        if len(body) > self.MAX_PAYLOAD_BYTES:
            raise ValidationError("Inbound webhook payload exceeds the safety limit")
        try:
            event_uuid = uuid.UUID(event_id)
            timestamp_int = int(timestamp)
        except (ValueError, TypeError) as exc:
            raise ValidationError("Invalid webhook event id or timestamp") from exc
        current = now or datetime.now(UTC)
        if abs(int(current.timestamp()) - timestamp_int) > max(1, min(tolerance_seconds, 3600)):
            raise AuthorizationError("Webhook timestamp is outside the allowed window")
        expected = self.signature(secret, timestamp_int, event_uuid, body)
        if not hmac.compare_digest(expected, signature):
            raise AuthorizationError("Webhook signature is invalid")
        body_hash = hashlib.sha256(body).hexdigest()
        existing = self.db.scalar(
            select(WebhookInboundReceipt).where(
                WebhookInboundReceipt.tenant_id == self.tenant_id,
                WebhookInboundReceipt.endpoint_name == endpoint_name,
                WebhookInboundReceipt.event_id == event_uuid,
                WebhookInboundReceipt.deleted_at.is_(None),
            )
        )
        if existing is not None:
            raise ConflictError("Webhook event was already received")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValidationError("Webhook payload is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValidationError("Webhook payload must be an object")
        self.db.add(
            WebhookInboundReceipt(
                tenant_id=self.tenant_id,
                endpoint_name=endpoint_name[:180],
                event_id=event_uuid,
                body_sha256=body_hash,
                received_at=current,
                expires_at=current + timedelta(seconds=max(60, tolerance_seconds * 2)),
            )
        )
        self.db.flush()
        return payload

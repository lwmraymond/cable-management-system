from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.exceptions import AuthorizationError, ConflictError
from app.integration_models import WebhookDeliveryAttempt, WebhookInboundReceipt, WebhookOutbox
from app.services.signed_webhooks import SignedWebhookService

SECRET = "0123456789abcdef-super-secret"


def endpoint(env, *, max_attempts=3):
    return env.write(
        env.webhooks().create_endpoint,
        name=f"receiver-{max_attempts}",
        url="http://receiver.test/hook",
        secret_reference="env:WEBHOOK_TEST_SECRET",
        timeout_seconds=5.0,
        max_attempts=max_attempts,
        allow_http=True,
    )


def enqueue(env, endpoint_id, event_id=None):
    return env.write(
        env.webhooks().enqueue,
        endpoint_id=uuid.UUID(endpoint_id),
        event_type="cable.updated",
        payload={"cable_id": "C-1", "status": "installed"},
        event_id=event_id,
    )


def test_signature_covers_timestamp_event_and_raw_body():
    event_id = uuid.uuid4()
    body = b'{"a":1}'
    first = SignedWebhookService.signature(SECRET, 100, event_id, body)
    assert first.startswith("v1=") and len(first) == 67
    assert first != SignedWebhookService.signature(SECRET, 101, event_id, body)
    assert first != SignedWebhookService.signature(SECRET, 100, uuid.uuid4(), body)
    assert first != SignedWebhookService.signature(SECRET, 100, event_id, b'{"a":2}')


def test_dispatch_success_records_attempt_and_valid_signature(integration_env):
    target = endpoint(integration_env)
    queued = enqueue(integration_env, target["id"])
    observed = {}

    def handler(request: httpx.Request):
        observed["event"] = request.headers["X-SIM-Event-ID"]
        observed["signature"] = request.headers["X-SIM-Signature"]
        observed["timestamp"] = int(request.headers["X-SIM-Timestamp"])
        observed["body"] = request.content
        expected = SignedWebhookService.signature(
            SECRET,
            observed["timestamp"],
            uuid.UUID(observed["event"]),
            observed["body"],
        )
        assert observed["signature"] == expected
        return httpx.Response(204, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = integration_env.write(
        integration_env.webhooks().dispatch_one,
        uuid.UUID(queued["id"]),
        secret_resolver=lambda reference: SECRET,
        client=client,
        now=datetime(2026, 9, 9, 1, 2, 3, tzinfo=UTC),
    )
    client.close()
    assert result["state"] == "delivered" and result["attempts"] == 1
    assert len(result["delivery_attempts"]) == 1
    assert json.loads(observed["body"]) == {"cable_id": "C-1", "status": "installed"}
    assert integration_env.db.scalar(select(func.count()).select_from(WebhookDeliveryAttempt)) == 1


def test_retry_backoff_dead_letter_and_manual_requeue(integration_env):
    target = endpoint(integration_env, max_attempts=2)
    queued = enqueue(integration_env, target["id"])
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(503, request=request, text="unavailable")
    ))
    now = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)
    first = integration_env.write(
        integration_env.webhooks().dispatch_one,
        uuid.UUID(queued["id"]),
        secret_resolver=lambda reference: SECRET,
        client=client,
        now=now,
    )
    assert first["state"] == "retry" and first["attempts"] == 1
    due = datetime.fromisoformat(first["next_attempt_at"])
    assert due > now
    with pytest.raises(ConflictError, match="not due"):
        integration_env.write(
            integration_env.webhooks().dispatch_one,
            uuid.UUID(queued["id"]),
            secret_resolver=lambda reference: SECRET,
            client=client,
            now=now,
        )
    second = integration_env.write(
        integration_env.webhooks().dispatch_one,
        uuid.UUID(queued["id"]),
        secret_resolver=lambda reference: SECRET,
        client=client,
        now=due + timedelta(seconds=1),
    )
    client.close()
    assert second["state"] == "dead" and second["attempts"] == 2
    requeued = integration_env.write(
        integration_env.webhooks().requeue_dead,
        uuid.UUID(queued["id"]),
    )
    assert requeued["state"] == "retry"


def test_payload_checksum_mismatch_goes_dead_without_delivery(integration_env):
    target = endpoint(integration_env)
    queued = enqueue(integration_env, target["id"])
    row = integration_env.db.get(WebhookOutbox, uuid.UUID(queued["id"]))
    row.payload = {"tampered": True}
    integration_env.db.commit()
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ConflictError, match="checksum"):
        integration_env.write(
            integration_env.webhooks().dispatch_one,
            row.id,
            secret_resolver=lambda reference: SECRET,
            client=client,
        )
    client.close()
    assert called is False


def test_inbound_verification_rejects_invalid_stale_and_replay(integration_env):
    service = integration_env.webhooks()
    body = b'{"type":"netbox.test","value":1}'
    event_id = uuid.uuid4()
    now = datetime(2026, 9, 9, 1, 2, 3, tzinfo=UTC)
    timestamp = int(now.timestamp())
    signature = service.signature(SECRET, timestamp, event_id, body)
    accepted = integration_env.write(
        service.verify_inbound,
        endpoint_name="receiver",
        body=body,
        event_id=str(event_id),
        timestamp=str(timestamp),
        signature=signature,
        secret=SECRET,
        now=now,
    )
    assert accepted["value"] == 1
    with pytest.raises(ConflictError, match="already"):
        integration_env.write(
            service.verify_inbound,
            endpoint_name="receiver",
            body=body,
            event_id=str(event_id),
            timestamp=str(timestamp),
            signature=signature,
            secret=SECRET,
            now=now,
        )
    with pytest.raises(AuthorizationError, match="signature"):
        integration_env.write(
            service.verify_inbound,
            endpoint_name="receiver",
            body=body,
            event_id=str(uuid.uuid4()),
            timestamp=str(timestamp),
            signature="v1=" + "0" * 64,
            secret=SECRET,
            now=now,
        )
    stale_id = uuid.uuid4()
    stale_ts = timestamp - 301
    with pytest.raises(AuthorizationError, match="timestamp"):
        integration_env.write(
            service.verify_inbound,
            endpoint_name="receiver",
            body=body,
            event_id=str(stale_id),
            timestamp=str(stale_ts),
            signature=service.signature(SECRET, stale_ts, stale_id, body),
            secret=SECRET,
            now=now,
        )
    assert integration_env.db.scalar(select(func.count()).select_from(WebhookInboundReceipt)) == 1


def test_reader_cannot_manage_or_send(integration_env):
    reader = integration_env.webhooks(integration_env.reader)
    with pytest.raises(AuthorizationError):
        integration_env.write(
            reader.create_endpoint,
            name="denied",
            url="https://receiver.test/hook",
            secret_reference="env:DENIED",
        )
    target = endpoint(integration_env)
    with pytest.raises(AuthorizationError):
        integration_env.write(
            reader.enqueue,
            endpoint_id=uuid.UUID(target["id"]),
            event_type="denied",
            payload={},
        )

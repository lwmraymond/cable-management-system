"""ASGI idempotency boundary for durable PWA mutation replay.

Only successful non-streaming mutation responses are retained. A repeated key
with a different method/path/query/body is rejected with 409. Receipts are
scoped by tenant and store only credential hashes, never raw tokens or keys.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionLocal, set_postgres_tenant_context
from app.field_models import FieldMutationReceipt


class FieldIdempotencyMiddleware:
    MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
    MAX_REQUEST_BYTES = 2 * 1024 * 1024
    MAX_RESPONSE_BYTES = 1024 * 1024
    SAFE_REPLAY_HEADERS = {"content-type", "location", "etag", "x-request-id"}

    def __init__(
        self,
        app,
        *,
        session_factory: sessionmaker[Session] = SessionLocal,
        api_prefix: str = "/api/v1",
        retention_seconds: int = 7 * 24 * 3600,
    ):
        self.app = app
        self.session_factory = session_factory
        self.api_prefix = api_prefix.rstrip("/")
        self.retention_seconds = max(60, int(retention_seconds))

    @staticmethod
    def _headers(scope) -> dict[str, str]:
        return {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }

    @staticmethod
    def _hash(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def _scope_hash(self, headers: dict[str, str]) -> str:
        credential = headers.get("x-actor-id") or headers.get("authorization") or "anonymous"
        return self._hash(credential.encode("utf-8"))

    def _request_hash(self, scope, body: bytes) -> str:
        query = scope.get("query_string", b"")
        material = b"\0".join([
            scope["method"].encode("ascii"),
            scope.get("path", "").encode("utf-8"),
            query,
            body,
        ])
        return self._hash(material)

    async def _read_body(self, receive) -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        total = 0
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.MAX_REQUEST_BYTES:
                return b"", False
            chunks.append(chunk)
            more = message.get("more_body", False)
        return b"".join(chunks), True

    @staticmethod
    def _receive_from(body: bytes):
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        return receive

    @staticmethod
    async def _json_response(send, status: int, detail: str, extra_headers=None):
        body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ]
        headers.extend(extra_headers or [])
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _replay(self, receipt: FieldMutationReceipt, send):
        headers = [
            (b"content-type", receipt.response_content_type.encode("latin-1")),
            (b"content-length", str(len(receipt.response_body.encode("utf-8"))).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"idempotency-replayed", b"true"),
        ]
        for key, value in receipt.response_headers.items():
            if key.lower() in self.SAFE_REPLAY_HEADERS and key.lower() != "content-type":
                headers.append((key.lower().encode("latin-1"), str(value).encode("latin-1")))
        await send({"type": "http.response.start", "status": receipt.response_status, "headers": headers})
        await send({
            "type": "http.response.body",
            "body": receipt.response_body.encode("utf-8"),
            "more_body": False,
        })

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        if method not in self.MUTATING_METHODS or not path.startswith(self.api_prefix):
            await self.app(scope, receive, send)
            return

        headers = self._headers(scope)
        raw_key = headers.get("idempotency-key", "").strip()
        raw_tenant = headers.get("x-tenant-id", "").strip()
        if not raw_key or not raw_tenant:
            await self.app(scope, receive, send)
            return
        if not 8 <= len(raw_key) <= 200:
            await self._json_response(send, 422, "Idempotency-Key must contain 8 to 200 characters")
            return
        try:
            tenant_id = uuid.UUID(raw_tenant)
        except ValueError:
            await self._json_response(send, 422, "X-Tenant-ID must be a UUID")
            return

        body, within_limit = await self._read_body(receive)
        if not within_limit:
            await self._json_response(send, 413, "Mutation body exceeds idempotency safety limit")
            return
        request_hash = self._request_hash(scope, body)
        key_hash = self._hash(f"{tenant_id}\0{raw_key}".encode("utf-8"))
        actor_scope_hash = self._scope_hash(headers)
        now = datetime.now(UTC)

        with self.session_factory() as db:
            set_postgres_tenant_context(db, tenant_id)
            existing = db.scalar(
                select(FieldMutationReceipt).where(
                    FieldMutationReceipt.tenant_id == tenant_id,
                    FieldMutationReceipt.idempotency_key_hash == key_hash,
                    FieldMutationReceipt.deleted_at.is_(None),
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash or existing.actor_scope_hash != actor_scope_hash:
                    await self._json_response(
                        send,
                        409,
                        "Idempotency-Key was already used for a different mutation or actor scope",
                    )
                    return
                await self._replay(existing, send)
                return

        messages: list[dict[str, Any]] = []

        async def capture(message):
            messages.append(message)

        await self.app(scope, self._receive_from(body), capture)
        start = next((message for message in messages if message["type"] == "http.response.start"), None)
        body_messages = [message for message in messages if message["type"] == "http.response.body"]
        if start is None:
            for message in messages:
                await send(message)
            return
        response_body = b"".join(message.get("body", b"") for message in body_messages)
        complete = not any(message.get("more_body", False) for message in body_messages)
        status = int(start["status"])
        should_store = 200 <= status < 300 and complete and len(response_body) <= self.MAX_RESPONSE_BYTES

        if should_store:
            raw_headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in start.get("headers", [])
            }
            content_type = raw_headers.get("content-type", "application/octet-stream")[:200]
            safe_headers = {
                key: value
                for key, value in raw_headers.items()
                if key in self.SAFE_REPLAY_HEADERS
            }
            receipt = FieldMutationReceipt(
                tenant_id=tenant_id,
                idempotency_key_hash=key_hash,
                request_hash=request_hash,
                method=method,
                path=path[:1000],
                actor_scope_hash=actor_scope_hash,
                response_status=status,
                response_content_type=content_type,
                response_body=response_body.decode("utf-8", errors="replace"),
                response_headers=safe_headers,
                completed_at=now,
                expires_at=now + timedelta(seconds=self.retention_seconds),
            )
            with self.session_factory() as db:
                set_postgres_tenant_context(db, tenant_id)
                db.add(receipt)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    winner = db.scalar(
                        select(FieldMutationReceipt).where(
                            FieldMutationReceipt.tenant_id == tenant_id,
                            FieldMutationReceipt.idempotency_key_hash == key_hash,
                            FieldMutationReceipt.deleted_at.is_(None),
                        )
                    )
                    if winner is not None and (
                        winner.request_hash != request_hash
                        or winner.actor_scope_hash != actor_scope_hash
                    ):
                        await self._json_response(
                            send,
                            409,
                            "Concurrent idempotency key collision",
                        )
                        return

        for message in messages:
            if message["type"] == "http.response.start" and should_store:
                copied = dict(message)
                copied["headers"] = list(message.get("headers", [])) + [
                    (b"idempotency-key-accepted", b"true")
                ]
                await send(copied)
            else:
                await send(message)

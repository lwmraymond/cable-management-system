from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.field_idempotency import FieldIdempotencyMiddleware
from app.field_models import FieldMutationReceipt
from app.models import Base, Organization, OrganizationType, Tenant


@pytest.fixture
def idempotent_client(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'idempotency.db'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enforce_fks(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with factory() as db:
        org = Organization(name="Idempotency", organization_type=OrganizationType.CUSTOMER)
        db.add(org); db.flush()
        first = Tenant(owner_organization_id=org.id, name="First", slug="idem-first")
        second = Tenant(owner_organization_id=org.id, name="Second", slug="idem-second")
        db.add_all([first, second]); db.commit()
        first_id, second_id = first.id, second.id

    app = FastAPI()
    calls = {"success": 0, "failure": 0}

    @app.post("/api/v1/mutate")
    async def mutate(request: Request):
        calls["success"] += 1
        return {"call": calls["success"], "body": await request.json()}

    @app.post("/api/v1/failure")
    def failure():
        calls["failure"] += 1
        raise HTTPException(503, "temporary")

    app.add_middleware(FieldIdempotencyMiddleware, session_factory=factory, retention_seconds=3600)
    with TestClient(app) as client:
        yield client, factory, calls, first_id, second_id
    engine.dispose()


def headers(tenant_id, key="11111111-2222-4333-8444-555555555555", actor=None):
    return {
        "X-Tenant-ID": str(tenant_id),
        "X-Actor-ID": str(actor or uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")),
        "Idempotency-Key": key,
    }


def test_success_is_executed_once_and_replayed(idempotent_client):
    client, factory, calls, tenant_id, _ = idempotent_client
    first = client.post("/api/v1/mutate", headers=headers(tenant_id), json={"value": 1})
    second = client.post("/api/v1/mutate", headers=headers(tenant_id), json={"value": 1})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json() == {"call": 1, "body": {"value": 1}}
    assert calls["success"] == 1
    assert first.headers["idempotency-key-accepted"] == "true"
    assert second.headers["idempotency-replayed"] == "true"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(FieldMutationReceipt)) == 1


def test_same_key_with_different_body_is_409(idempotent_client):
    client, _, calls, tenant_id, _ = idempotent_client
    assert client.post("/api/v1/mutate", headers=headers(tenant_id), json={"value": 1}).status_code == 200
    conflict = client.post("/api/v1/mutate", headers=headers(tenant_id), json={"value": 2})
    assert conflict.status_code == 409
    assert calls["success"] == 1


def test_same_key_with_different_actor_is_409(idempotent_client):
    client, _, calls, tenant_id, _ = idempotent_client
    key = "fixed-key-00000001"
    assert client.post("/api/v1/mutate", headers=headers(tenant_id, key), json={}).status_code == 200
    other_actor = uuid.UUID("bbbbbbbb-cccc-4ddd-8eee-ffffffffffff")
    conflict = client.post(
        "/api/v1/mutate",
        headers=headers(tenant_id, key, other_actor),
        json={},
    )
    assert conflict.status_code == 409 and calls["success"] == 1


def test_key_is_tenant_scoped(idempotent_client):
    client, _, calls, first_id, second_id = idempotent_client
    key = "tenant-shared-key"
    assert client.post("/api/v1/mutate", headers=headers(first_id, key), json={}).status_code == 200
    assert client.post("/api/v1/mutate", headers=headers(second_id, key), json={}).status_code == 200
    assert calls["success"] == 2


def test_5xx_is_not_cached_and_missing_key_passes_through(idempotent_client):
    client, factory, calls, tenant_id, _ = idempotent_client
    assert client.post("/api/v1/failure", headers=headers(tenant_id), json={}).status_code == 503
    assert client.post("/api/v1/failure", headers=headers(tenant_id), json={}).status_code == 503
    assert calls["failure"] == 2
    assert client.post("/api/v1/mutate", json={"unkeyed": True}).status_code == 200
    assert client.post("/api/v1/mutate", json={"unkeyed": True}).status_code == 200
    assert calls["success"] == 2
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(FieldMutationReceipt)) == 0


def test_invalid_key_and_body_limit_are_rejected_before_handler(idempotent_client):
    client, _, calls, tenant_id, _ = idempotent_client
    short = client.post("/api/v1/mutate", headers=headers(tenant_id, "short"), json={})
    assert short.status_code == 422
    oversized = client.post(
        "/api/v1/mutate",
        headers=headers(tenant_id, "oversized-key"),
        content=b"x" * (FieldIdempotencyMiddleware.MAX_REQUEST_BYTES + 1),
    )
    assert oversized.status_code == 413
    assert calls["success"] == 0

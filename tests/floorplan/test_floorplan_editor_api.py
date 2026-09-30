from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.floorplan_editor import build_floorplan_router
from app.db import set_postgres_tenant_context
from app.exceptions import DomainError
from app.security import Principal


@pytest.fixture
def floor_client(floor_env):
    app = FastAPI()

    def get_db():
        with floor_env.factory() as session:
            set_postgres_tenant_context(session, floor_env.tenant.id)
            yield session

    def get_principal(request: Request):
        if request.headers.get("X-Test-Reader"):
            return Principal(
                floor_env.reader.id,
                floor_env.reader.organization_id,
                floor_env.tenant.id,
                frozenset({"*"}),
                "Forged cache",
                True,
            )
        return floor_env.principal

    app.include_router(build_floorplan_router(get_db, get_principal), prefix="/api/v1")

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    with TestClient(app) as client:
        yield client


def create_api_plan(client, env):
    response = client.post(
        "/api/v1/floor-plans",
        json={
            "project_id": str(env.project.id),
            "location_id": str(env.floor.id),
            "name": "API floor",
            "width_mm": 30000.0,
            "height_mm": 20000.0,
            "grid_mm": 500.0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_http_create_save_publish_restore_and_history(floor_client, floor_env):
    plan = create_api_plan(floor_client, floor_env)
    document = plan["document"]
    document["objects"].append({
        "client_id": "note-1",
        "kind": "annotation",
        "resource_id": None,
        "label": "Exit",
        "x_mm": 1000.0,
        "y_mm": 1000.0,
        "width_mm": 1000.0,
        "height_mm": 500.0,
        "rotation_deg": 0.0,
        "z_index": 0,
        "geometry": {"text": "Exit"},
    })
    saved = floor_client.post(
        f"/api/v1/floor-plans/{plan['id']}/revisions",
        json={"expected_version": 1, "document": document, "note": "API save"},
    )
    assert saved.status_code == 201 and saved.json()["revision"] == 2
    published = floor_client.post(
        f"/api/v1/floor-plans/{plan['id']}/publish",
        json={"expected_version": 2, "revision": 2},
    )
    assert published.status_code == 200 and published.json()["published_revision"] == 2
    restored = floor_client.post(
        f"/api/v1/floor-plans/{plan['id']}/restore",
        json={"expected_version": 3, "source_revision": 1, "note": "Undo"},
    )
    assert restored.status_code == 201 and restored.json()["revision"] == 3
    history = floor_client.get(f"/api/v1/floor-plans/{plan['id']}/revisions")
    assert history.status_code == 200
    assert [row["revision"] for row in history.json()["items"]] == [3, 2, 1]


def test_http_stale_write_returns_409_and_preserves_current_head(floor_client, floor_env):
    plan = create_api_plan(floor_client, floor_env)
    first = floor_client.post(
        f"/api/v1/floor-plans/{plan['id']}/revisions",
        json={"expected_version": 1, "document": plan["document"], "note": "first"},
    )
    assert first.status_code == 201
    stale = floor_client.post(
        f"/api/v1/floor-plans/{plan['id']}/revisions",
        json={"expected_version": 1, "document": plan["document"], "note": "stale"},
    )
    assert stale.status_code == 409
    current = floor_client.get(f"/api/v1/floor-plans/{plan['id']}").json()
    assert current["version"] == 2 and current["head_revision"] == 2


def test_http_strict_payload_and_permission_boundaries(floor_client, floor_env):
    invalid = floor_client.post(
        "/api/v1/floor-plans",
        json={
            "project_id": str(floor_env.project.id),
            "location_id": str(floor_env.floor.id),
            "name": "bad",
            "width_mm": True,
            "height_mm": 1000.0,
            "tenant_id": str(floor_env.other_tenant.id),
        },
    )
    assert invalid.status_code == 422
    denied = floor_client.post(
        "/api/v1/floor-plans",
        headers={"X-Test-Reader": "1"},
        json={
            "project_id": str(floor_env.project.id),
            "location_id": str(floor_env.floor.id),
            "name": "denied",
            "width_mm": 1000.0,
            "height_mm": 1000.0,
        },
    )
    assert denied.status_code == 403
    assert floor_client.get(f"/api/v1/floor-plans/{uuid.uuid4()}").status_code == 404


def test_openapi_has_seven_floor_plan_operations(floor_client):
    paths = floor_client.get("/openapi.json").json()["paths"]
    operations = sum(
        1
        for path, methods in paths.items()
        if path.startswith("/api/v1/floor-plans")
        for method in methods
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    )
    assert operations == 7

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.floorplan import build_floorplan_router
from app.db import set_postgres_tenant_context
from app.exceptions import DomainError
from app.security import Principal


@pytest.fixture
def client(env):
    app = FastAPI()
    tenant_id = env.tenants[0].id

    def get_db():
        with env.factory() as session:
            set_postgres_tenant_context(session, tenant_id)
            yield session

    def get_principal(request: Request):
        if request.headers.get("X-Test-Reader"):
            return Principal(
                env.reader.id,
                env.reader.organization_id,
                tenant_id,
                frozenset({"*"}),
                "Forged cache",
                True,
            )
        return env.principal

    app.include_router(build_floorplan_router(get_db, get_principal), prefix="/api/v1")

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    with TestClient(app) as test_client:
        yield test_client


def create_plan(client, env):
    response = client.post("/api/v1/floor-plans", json={
        "project_id": str(env.projects[0].id),
        "location_id": str(env.floors[0].id),
        "name": "API Floor",
        "units": "mm",
        "canvas_width": 1000.0,
        "canvas_height": 800.0,
        "background_reference": "tenant/api-floor.svg",
    })
    assert response.status_code == 201, response.text
    return response.json()


def document(env):
    return {
        "schema_version": 1,
        "grid_size": 25,
        "objects": [{
            "id": "rack-api",
            "object_type": "rack",
            "object_id": str(env.racks[0].id),
            "x": 100,
            "y": 100,
            "width": 60,
            "height": 100,
            "rotation": 0,
            "z_index": 1,
            "locked": False,
            "label": "Rack API",
        }],
        "paths": [{
            "id": "path-api",
            "pathway_id": str(env.pathways[0].id),
            "points": [{"x": 0, "y": 0}, {"x": 300, "y": 300}],
            "width": 5,
            "label": "Tray",
        }],
    }


def test_http_create_list_get_save_publish_history_restore(client, env):
    created = create_plan(client, env)
    plan_id = created["id"]
    listed = client.get("/api/v1/floor-plans", params={
        "project_id": str(env.projects[0].id),
        "location_id": str(env.floors[0].id),
    })
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == plan_id
    fetched = client.get(f"/api/v1/floor-plans/{plan_id}")
    assert fetched.status_code == 200 and fetched.json()["version"] == 1

    saved = client.put(f"/api/v1/floor-plans/{plan_id}/draft", json={
        "expected_version": 1,
        "document": document(env),
        "change_summary": "Place rack",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    published = client.post(f"/api/v1/floor-plans/{plan_id}/publish", json={
        "expected_version": 2,
    })
    assert published.status_code == 200
    assert published.json()["published_revision_number"] == 2

    history = client.get(f"/api/v1/floor-plans/{plan_id}/revisions")
    assert history.status_code == 200
    assert [row["revision_number"] for row in history.json()["items"]] == [2, 1]
    initial_id = history.json()["items"][1]["id"]
    restored = client.post(
        f"/api/v1/floor-plans/{plan_id}/revisions/{initial_id}/restore",
        json={"expected_version": 3, "change_summary": "Rollback"},
    )
    assert restored.status_code == 200
    assert restored.json()["version"] == 4
    assert restored.json()["document"]["objects"] == []


def test_http_strict_payload_stale_conflict_and_permissions(client, env):
    created = create_plan(client, env)
    plan_id = created["id"]
    extra = client.post("/api/v1/floor-plans", json={
        "project_id": str(env.projects[0].id),
        "location_id": str(env.floors[0].id),
        "name": "Extra",
        "units": "mm",
        "canvas_width": 1000.0,
        "canvas_height": 800.0,
        "tenant_id": str(env.tenants[1].id),
    })
    assert extra.status_code == 422
    boolean_dimension = client.post("/api/v1/floor-plans", json={
        "project_id": str(env.projects[0].id),
        "location_id": str(env.floors[0].id),
        "name": "Boolean",
        "units": "mm",
        "canvas_width": True,
        "canvas_height": 800.0,
    })
    assert boolean_dimension.status_code == 422

    saved = client.put(f"/api/v1/floor-plans/{plan_id}/draft", json={
        "expected_version": 1,
        "document": document(env),
    })
    assert saved.status_code == 200
    stale = client.put(f"/api/v1/floor-plans/{plan_id}/draft", json={
        "expected_version": 1,
        "document": {**document(env), "grid_size": 50},
    })
    assert stale.status_code == 409
    assert client.put(
        f"/api/v1/floor-plans/{plan_id}/draft",
        json={"expected_version": 2, "document": document(env)},
        headers={"X-Test-Reader": "1"},
    ).status_code == 403
    assert client.get(f"/api/v1/floor-plans/{uuid.uuid4()}").status_code == 404


def test_openapi_contains_seven_floor_plan_operations(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert len(paths) == 6
    operation_count = sum(
        method in {"get", "post", "put", "patch", "delete"}
        for value in paths.values()
        for method in value
    )
    assert operation_count == 7
    assert "/api/v1/floor-plans/{plan_id}/draft" in paths
    assert "/api/v1/floor-plans/{plan_id}/revisions/{revision_id}/restore" in paths

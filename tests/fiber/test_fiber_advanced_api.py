"""Real advanced routers + SQL/service; identity is injected, not OIDC proof."""
from __future__ import annotations

from datetime import UTC, datetime
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.fiber import build_fiber_router
from app.api.fiber_advanced import build_fiber_advanced_router
from app.db import set_postgres_tenant_context
from app.exceptions import DomainError
from app.security import Principal


@pytest.fixture
def advanced_client(env):
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

    app.include_router(build_fiber_router(get_db, get_principal), prefix="/api/v1")
    app.include_router(build_fiber_advanced_router(get_db, get_principal), prefix="/api/v1")

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    with TestClient(app) as test_client:
        yield test_client


def create_bundle(client, cable):
    response = client.post("/api/v1/fiber/bundles", json={
        "cable_id": str(cable.id),
        "name": cable.identifier,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_http_termination_generic_trace_stale_release_and_reuse(advanced_client, env):
    bundle = create_bundle(advanced_client, env.cables[0])
    body = {
        "strand_id": bundle["strands"][0]["id"],
        "side": "A",
        "port_id": str(env.fiber_ports[0].id),
        "connection_type": "connector",
        "loss_db": 0.1,
    }
    created = advanced_client.post("/api/v1/fiber/terminations", json=body)
    assert created.status_code == 201, created.text
    traced = advanced_client.get(
        f"/api/v1/fiber/cables/{env.cables[0].id}/trace",
        params={"strand_number": 1},
    )
    assert traced.status_code == 200, traced.text
    assert traced.json()["trace_model"] == "generic-fiber"
    assert "fiber_termination" in [row["kind"] for row in traced.json()["items"]]

    termination_id = created.json()["id"]
    stale = advanced_client.post(
        f"/api/v1/fiber/terminations/{termination_id}/release",
        json={"expected_version": 2},
    )
    assert stale.status_code == 409
    released = advanced_client.post(
        f"/api/v1/fiber/terminations/{termination_id}/release",
        json={"expected_version": 1},
    )
    assert released.status_code == 200 and released.json()["version"] == 2
    reused = advanced_client.post("/api/v1/fiber/terminations", json=body)
    assert reused.status_code == 201


def test_http_pair_channel_get_trace_release(advanced_client, env):
    provisioned = advanced_client.post(
        f"/api/v1/fiber/cables/{env.copper_cables[0].id}/pairs/provision"
    )
    assert provisioned.status_code == 201, provisioned.text
    pairs = provisioned.json()["pairs"]
    listed = advanced_client.get(
        f"/api/v1/fiber/cables/{env.copper_cables[0].id}/pairs"
    )
    assert listed.status_code == 200 and listed.json()["pair_count"] == 4

    channel = advanced_client.post("/api/v1/fiber/channels", json={
        "project_id": str(env.projects[0].id),
        "identifier": "API-CU-01",
        "name": "API copper channel",
        "medium": "copper",
        "topology": "ethernet",
        "status": "active",
        "members": [
            {"kind": "copper_pair", "resource_id": pairs[0]["id"], "role": "pair-1"},
            {"kind": "copper_pair", "resource_id": pairs[1]["id"], "role": "pair-2"},
        ],
    })
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    fetched = advanced_client.get(f"/api/v1/fiber/channels/{channel_id}")
    assert fetched.status_code == 200 and len(fetched.json()["members"]) == 2
    trace = advanced_client.get(f"/api/v1/fiber/channels/{channel_id}/trace")
    assert trace.status_code == 422
    released = advanced_client.post(
        f"/api/v1/fiber/channels/{channel_id}/release",
        json={"expected_version": 1},
    )
    assert released.status_code == 200 and released.json()["version"] == 2


def test_http_breakout_and_otdr_link_are_persistent(advanced_client, env):
    first = create_bundle(advanced_client, env.cables[0])
    second = create_bundle(advanced_client, env.cables[1])
    breakout = advanced_client.post("/api/v1/fiber/breakouts", json={
        "project_id": str(env.projects[0].id),
        "device_id": str(env.devices[0].id),
        "identifier": "API-BO-01",
        "name": "API breakout",
        "mode": "passive",
        "legs": [{
            "parent_strand_id": first["strands"][0]["id"],
            "parent_side": "B",
            "child_strand_id": second["strands"][0]["id"],
            "child_side": "A",
            "label": "leg-1",
            "loss_db": 0.05,
        }],
    })
    assert breakout.status_code == 201, breakout.text
    breakout_id = breakout.json()["id"]
    fetched = advanced_client.get(f"/api/v1/fiber/breakouts/{breakout_id}")
    assert fetched.status_code == 200 and fetched.json()["legs"][0]["label"] == "leg-1"

    record = advanced_client.post("/api/v1/fiber/otdr-records", json={
        "project_id": str(env.projects[0].id),
        "cable_id": str(env.cables[0].id),
        "strand_id": first["strands"][0]["id"],
        "direction": "A",
        "wavelength_nm": 1550,
        "acquired_at": datetime.now(UTC).isoformat(),
        "source_name": "api-trace.sor",
        "total_length_m": 50.0,
        "end_to_end_loss_db": 0.8,
        "metadata": {"instrument": "api-test"},
        "events": [
            {"event_type": "connector", "distance_m": 0.0, "loss_db": 0.1},
            {"event_type": "end", "distance_m": 50.0},
        ],
    })
    assert record.status_code == 201, record.text
    event_id = record.json()["events"][0]["id"]
    linked = advanced_client.post(f"/api/v1/fiber/otdr-events/{event_id}/link", json={
        "linked_kind": "breakout_leg",
        "linked_id": breakout.json()["legs"][0]["id"],
        "expected_version": 1,
        "link_offset_m": 0.25,
    })
    assert linked.status_code == 200, linked.text
    assert linked.json()["version"] == 2
    stored = advanced_client.get(f"/api/v1/fiber/otdr-records/{record.json()['id']}")
    assert stored.status_code == 200
    assert stored.json()["events"][0]["linked_kind"] == "breakout_leg"


def test_http_strict_payload_conflict_and_fresh_permission_boundaries(advanced_client, env):
    first = create_bundle(advanced_client, env.cables[0])
    valid = {
        "strand_id": first["strands"][0]["id"],
        "side": "A",
        "port_id": str(env.fiber_ports[0].id),
        "loss_db": 0.1,
    }
    assert advanced_client.post(
        "/api/v1/fiber/terminations",
        json={**valid, "tenant_id": str(env.tenants[1].id)},
    ).status_code == 422
    assert advanced_client.post(
        "/api/v1/fiber/terminations",
        json={**valid, "loss_db": True},
    ).status_code == 422
    assert advanced_client.post("/api/v1/fiber/terminations", json=valid).status_code == 201
    collision = dict(valid)
    collision["strand_id"] = first["strands"][1]["id"]
    assert advanced_client.post("/api/v1/fiber/terminations", json=collision).status_code == 409
    assert advanced_client.get(
        f"/api/v1/fiber/cables/{env.cables[0].id}/trace",
        params={"strand_number": 1},
        headers={"X-Test-Reader": "true"},
    ).status_code == 403
    assert advanced_client.post("/api/v1/fiber/otdr-records", json={
        "project_id": str(env.projects[0].id),
        "cable_id": str(env.cables[0].id),
        "direction": "A",
        "wavelength_nm": 1550,
        "acquired_at": "2026-09-04T12:00:00",
        "source_name": "naive.sor",
    }).status_code == 422


def test_openapi_exposes_basic_and_advanced_routes_without_workflows(advanced_client):
    paths = advanced_client.get("/openapi.json").json()["paths"]
    assert len(paths) == 24
    for path in (
        "/api/v1/fiber/terminations",
        "/api/v1/fiber/channels/{channel_id}/trace",
        "/api/v1/fiber/breakouts",
        "/api/v1/fiber/otdr-events/{event_id}/link",
        "/api/v1/fiber/cables/{cable_id}/trace",
    ):
        assert path in paths

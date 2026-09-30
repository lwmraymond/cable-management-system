"""Real router + real service/SQL; identity injection excludes OIDC/middleware claims."""
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.fiber import build_fiber_router
from app.db import set_postgres_tenant_context
from app.exceptions import DomainError
from app.fiber_models import FiberSplice, FiberSpliceEnd
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
            return Principal(env.reader.id, env.reader.organization_id, tenant_id,
                             frozenset({"*"}), "Forged cache", True)
        return env.principal

    app.include_router(build_fiber_router(get_db, get_principal), prefix="/api/v1")

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    with TestClient(app) as test_client:
        yield test_client


def setup_api(client, env):
    bundles = []
    for cable in env.cables[:3]:
        response = client.post('/api/v1/fiber/bundles', json={
            'cable_id': str(cable.id), 'name': cable.identifier})
        assert response.status_code == 201, response.text
        bundles.append(response.json())
    result = client.post('/api/v1/fiber/cassettes', json={
        'device_id': str(env.devices[0].id), 'project_id': str(env.projects[0].id),
        'name': 'API cassette', 'slot_count': 4})
    assert result.status_code == 201, result.text
    return bundles, result.json()


def test_http_create_read_lookup_list_trace_release(client, env):
    bundles, cassette = setup_api(client, env)
    found = client.get(f'/api/v1/fiber/cables/{env.cables[0].id}/bundle')
    assert found.status_code == 200 and found.json()['id'] == bundles[0]['id']
    listed = client.get(f'/api/v1/fiber/devices/{env.devices[0].id}/cassettes',
                        params={'project_id': str(env.projects[0].id)})
    assert listed.status_code == 200 and len(listed.json()['items']) == 1
    slot = cassette['slots'][0]['id']
    response = client.post(f'/api/v1/fiber/slots/{slot}/splice', json={
        'left_strand_id': bundles[0]['strands'][0]['id'], 'left_side': 'B',
        'right_strand_id': bundles[1]['strands'][0]['id'], 'right_side': 'A',
        'loss_db': 0.2, 'expected_version': 1})
    assert response.status_code == 201, response.text
    current = client.get(f'/api/v1/fiber/cassettes/{cassette["id"]}').json()
    assert current['slots'][0]['version'] == 2
    trace = client.get(f'/api/v1/fiber/strands/{bundles[0]["strands"][0]["id"]}/trace')
    assert trace.status_code == 200 and trace.json()['total_splice_loss_db'] == 0.2
    stale = client.post(f'/api/v1/fiber/slots/{slot}/release', json={'expected_version': 1})
    assert stale.status_code == 409
    released = client.post(f'/api/v1/fiber/slots/{slot}/release', json={'expected_version': 2})
    assert released.status_code == 200 and released.json()['slot_version'] == 3


def test_http_rejects_unknown_tenant_override_and_invalid_types(client, env):
    body = {'cable_id': str(env.cables[0].id), 'name': 'x',
            'tenant_id': str(env.tenants[1].id)}
    assert client.post('/api/v1/fiber/bundles', json=body).status_code == 422
    cassette = {'device_id': str(env.devices[0].id), 'project_id': str(env.projects[0].id),
                'name': 'x', 'slot_count': True}
    assert client.post('/api/v1/fiber/cassettes', json=cassette).status_code == 422
    assert client.get(f'/api/v1/fiber/strands/{uuid.uuid4()}/trace',
                      params={'max_hops': 257}).status_code == 422


def test_http_403_and_404_boundaries(client, env):
    assert client.post('/api/v1/fiber/bundles',
                       json={'cable_id': str(env.cables[0].id), 'name': 'denied'},
                       headers={'X-Test-Reader': 'true'}).status_code == 403
    assert client.post('/api/v1/fiber/bundles',
                       json={'cable_id': str(env.cables[8].id), 'name': 'foreign'}).status_code == 404
    assert client.get(f'/api/v1/fiber/bundles/{uuid.uuid4()}').status_code == 404


def test_http_conflict_rolls_back_version_claims_and_audit(client, env):
    bundles, cassette = setup_api(client, env)
    body = {'left_strand_id': bundles[0]['strands'][0]['id'], 'left_side': 'B',
            'right_strand_id': bundles[1]['strands'][0]['id'], 'right_side': 'A',
            'expected_version': 1}
    assert client.post(f'/api/v1/fiber/slots/{cassette["slots"][0]["id"]}/splice',
                       json=body).status_code == 201
    body['right_strand_id'] = bundles[2]['strands'][0]['id']
    assert client.post(f'/api/v1/fiber/slots/{cassette["slots"][1]["id"]}/splice',
                       json=body).status_code == 409
    current = client.get(f'/api/v1/fiber/cassettes/{cassette["id"]}').json()
    assert current['slots'][1]['version'] == 1 and current['slots'][1]['splice_id'] is None
    env.db.expire_all()
    assert env.db.scalar(select(func.count()).select_from(FiberSplice)) == 1
    assert env.db.scalar(select(func.count()).select_from(FiberSpliceEnd)) == 2


def test_router_openapi_contains_all_nine_routes(client):
    paths = client.get('/openapi.json').json()['paths']
    assert len(paths) == 9
    assert '/api/v1/fiber/slots/{slot_id}/splice' in paths

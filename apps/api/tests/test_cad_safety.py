import asyncio
import io

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.cad_upload_limit import CadUploadLimitMiddleware
from test_migration_policy_coverage import load_migration


@pytest.mark.parametrize("length", [True, False])
def test_upload_is_bounded_before_multipart_spooling(length):
    consumed = 0
    sent = []

    async def receive():
        nonlocal consumed
        consumed += 1
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

    async def send(message):
        sent.append(message)

    async def inner(scope, receive, send):
        while True:
            await receive()

    app = CadUploadLimitMiddleware(inner)
    app.limit = 2048
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/scene/cad/imports",
        "headers": [(b"content-length", b"1000000000")] if length else [],
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 413
    assert consumed == (0 if length else 3)


def test_upload_stream_limit_retains_multipart_cleanup_and_413():
    from fastapi import FastAPI, File, UploadFile
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(CadUploadLimitMiddleware)

    @app.post("/scene/cad/imports")
    async def upload(file: UploadFile = File(...)):
        raise AssertionError("oversize upload reached business handler")

    payload = b'--test\r\nContent-Disposition: form-data; name="file"; filename="a.dxf"\r\nContent-Type: application/octet-stream\r\n\r\n'

    def chunks():
        yield payload
        for _ in range(10):
            yield b"x" * 1024 * 1024
        yield b"\r\n--test--\r\n"

    response = TestClient(app).post(
        "/scene/cad/imports",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=test"},
    )
    assert response.status_code == 413, response.text


def test_cad_postgres_policies_and_fail_closed_downgrade():
    migration, _ = load_migration("200000000013_cad_exchange.py")
    output = io.StringIO()
    migration.op = Operations(
        MigrationContext.configure(
            dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
        )
    )
    migration.upgrade()
    for table in migration.TENANT_TABLES:
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in output.getvalue()
    before = output.getvalue()
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()
    assert output.getvalue() == before

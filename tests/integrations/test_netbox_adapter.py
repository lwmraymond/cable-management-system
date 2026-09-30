from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.integration_models import ExternalObjectMap, NetBoxSyncCursor
from app.services.netbox_adapter import NetBoxClient


class FakeSyncClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch_all(self, resource, params=None):
        self.calls.append((resource, dict(params or {})))
        return list(self.rows)


def test_client_paginates_same_origin_and_sends_token():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        assert request.headers["Authorization"] == "Token secret-token"
        if len(seen) == 1:
            assert request.url.params["limit"] == "200"
            return httpx.Response(
                200,
                request=request,
                json={
                    "results": [{"id": 1}],
                    "next": "https://netbox.test/api/dcim/devices/?limit=200&offset=200",
                },
            )
        return httpx.Response(200, request=request, json={"results": [{"id": 2}], "next": None})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = NetBoxClient("https://netbox.test/", "secret-token", client=http)
    assert client.fetch_all("dcim/devices") == [{"id": 1}, {"id": 2}]
    assert len(seen) == 2
    http.close()


def test_client_rejects_cross_origin_next_link():
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            request=request,
            json={"results": [], "next": "https://evil.invalid/api/dcim/devices/"},
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = NetBoxClient("https://netbox.test/", "secret-token", client=http)
    with pytest.raises(ValidationError, match="origin"):
        client.fetch_all("dcim/devices")
    http.close()


def test_client_honors_bounded_429_retry_after():
    calls = 0
    sleeps = []

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, request=request, headers={"Retry-After": "0.25"})
        return httpx.Response(200, request=request, json={"results": [], "next": None})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = NetBoxClient(
        "https://netbox.test/",
        "secret-token",
        client=http,
        sleep=sleeps.append,
    )
    assert client.fetch_all("dcim/devices") == []
    assert calls == 3 and sleeps == [0.25, 0.25]
    http.close()


def test_client_resource_and_record_limits_are_enforced():
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, request=request, json={"results": [{"id": 1}, {"id": 2}], "next": None}
    )))
    client = NetBoxClient("https://netbox.test/", "secret-token", client=http, max_records=1)
    with pytest.raises(ValidationError, match="record safety"):
        client.fetch_all("dcim/devices")
    with pytest.raises(ValidationError, match="Unsupported"):
        client.fetch_all("users/users")
    http.close()


def test_sync_persists_cursor_and_upserts_external_map(integration_env):
    row = {
        "id": 101,
        "display": "Mapped device",
        "url": "https://netbox.test/api/dcim/devices/101/",
        "last_updated": "2026-09-09T01:02:03Z",
        "custom_fields": {
            "sim_local_type": "device",
            "sim_local_id": str(integration_env.device.id),
        },
    }
    fake = FakeSyncClient([row, {"id": 102, "custom_fields": {}}])
    result = integration_env.write(
        integration_env.netbox().sync,
        name="primary",
        resource="dcim/devices",
        base_url="https://netbox.test",
        project_id=integration_env.project.id,
        location_id=integration_env.location.id,
        client=fake,
    )
    assert result["status"] == "succeeded"
    assert result["records"] == 2 and result["mapped"] == 1 and result["skipped"] == 1
    assert result["cursor"] == "2026-09-09T01:02:03Z"
    assert fake.calls == [("dcim/devices", {})]
    assert integration_env.db.scalar(select(func.count()).select_from(ExternalObjectMap)) == 1
    mapping = integration_env.db.scalar(select(ExternalObjectMap))
    assert mapping.local_id == integration_env.device.id
    assert mapping.external_id == "101"
    assert len(mapping.snapshot_checksum) == 64

    changed = dict(row)
    changed["display"] = "Renamed"
    changed["last_updated"] = "2026-09-10T01:02:03Z"
    second = FakeSyncClient([changed])
    integration_env.write(
        integration_env.netbox().sync,
        name="primary",
        resource="dcim/devices",
        base_url="https://netbox.test",
        project_id=integration_env.project.id,
        location_id=integration_env.location.id,
        client=second,
    )
    assert second.calls[0][1] == {"last_updated__gte": "2026-09-09T01:02:03Z"}
    assert integration_env.db.scalar(select(func.count()).select_from(ExternalObjectMap)) == 1
    integration_env.db.expire_all()
    mapping = integration_env.db.scalar(select(ExternalObjectMap))
    assert mapping.metadata_json["display"] == "Renamed"
    cursor = integration_env.db.scalar(select(NetBoxSyncCursor))
    assert cursor.cursor_value == "2026-09-10T01:02:03Z"


def test_sync_rejects_invalid_mapping_and_scope(integration_env):
    invalid = FakeSyncClient([{
        "id": 1,
        "custom_fields": {"sim_local_type": "device", "sim_local_id": "not-a-uuid"},
    }])
    with pytest.raises(ValidationError, match="UUID"):
        integration_env.write(
            integration_env.netbox().sync,
            name="bad",
            resource="dcim/devices",
            base_url="https://netbox.test",
            project_id=integration_env.project.id,
            location_id=integration_env.location.id,
            client=invalid,
        )
    with pytest.raises(NotFoundError):
        integration_env.write(
            integration_env.netbox().sync,
            name="foreign",
            resource="dcim/devices",
            base_url="https://netbox.test",
            project_id=uuid.uuid4(),
            location_id=integration_env.location.id,
            client=FakeSyncClient([]),
        )


def test_reader_cannot_sync_despite_forged_cached_permissions(integration_env):
    with pytest.raises(AuthorizationError):
        integration_env.write(
            integration_env.netbox(integration_env.reader).sync,
            name="denied",
            resource="dcim/devices",
            base_url="https://netbox.test",
            project_id=integration_env.project.id,
            location_id=integration_env.location.id,
            client=FakeSyncClient([]),
        )

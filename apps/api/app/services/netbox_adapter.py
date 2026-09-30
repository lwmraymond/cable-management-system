"""Bounded, same-origin NetBox API adapter and auditable snapshot mapper."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.integration_models import ExternalObjectMap, NetBoxSyncCursor
from app.models import Location, Project, Tenant
from app.security import Principal, require_permission, resolve_principal


class NetBoxClient:
    ALLOWED_RESOURCES = {
        "dcim/devices",
        "dcim/interfaces",
        "dcim/cables",
        "dcim/racks",
        "dcim/locations",
    }

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        allow_http: bool = False,
        timeout_seconds: float = 10.0,
        max_pages: int = 100,
        max_records: int = 20_000,
        sleep: Callable[[float], None] = time.sleep,
    ):
        parsed = urlparse(base_url)
        if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}):
            raise ValidationError("NetBox base URL must use HTTPS")
        if not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise ValidationError("Invalid NetBox base URL")
        if not isinstance(token, str) or not token.strip() or len(token) > 4096:
            raise ValidationError("NetBox token is required")
        self.base_url = base_url.rstrip("/") + "/"
        self.origin = (parsed.scheme.lower(), parsed.netloc.lower())
        self.base_path = parsed.path.rstrip("/") + "/"
        self.token = token.strip()
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        self.max_pages = max(1, min(int(max_pages), 1000))
        self.max_records = max(1, min(int(max_records), 100_000))
        self.sleep = sleep
        self.client = client or httpx.Client(timeout=self.timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _url(self, resource: str) -> str:
        normalized = resource.strip("/")
        if normalized not in self.ALLOWED_RESOURCES:
            raise ValidationError("Unsupported NetBox resource")
        return urljoin(self.base_url, f"api/{normalized}/")

    def _safe_next(self, value: str) -> str:
        target = urlparse(urljoin(self.base_url, value))
        if (target.scheme.lower(), target.netloc.lower()) != self.origin:
            raise ValidationError("NetBox pagination attempted to leave the configured origin")
        expected_prefix = self.base_path.rstrip("/") + "/api/"
        if not target.path.startswith(expected_prefix):
            raise ValidationError("NetBox pagination path escaped the configured API root")
        return target.geturl()

    def _get(self, url: str, params: dict[str, Any] | None, *, max_429_retries: int = 3):
        retries = 0
        while True:
            response = self.client.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Token {self.token}",
                    "Accept": "application/json",
                    "User-Agent": "structured-infrastructure-manager/0.1 netbox-adapter",
                },
                timeout=self.timeout_seconds,
            )
            if response.status_code != 429:
                response.raise_for_status()
                return response
            if retries >= max_429_retries:
                raise httpx.HTTPStatusError(
                    "NetBox rate-limit retry budget exhausted",
                    request=response.request,
                    response=response,
                )
            raw_retry = response.headers.get("Retry-After", "1")
            try:
                delay = max(0.1, min(float(raw_retry), 30.0))
            except ValueError:
                delay = 1.0
            self.sleep(delay)
            retries += 1

    def fetch_all(self, resource: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        url = self._url(resource)
        query = dict(params or {})
        query.setdefault("limit", 200)
        records: list[dict[str, Any]] = []
        for page in range(self.max_pages):
            response = self._get(url, query if page == 0 else None)
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValidationError("NetBox returned an invalid paginated payload")
            for row in payload["results"]:
                if not isinstance(row, dict):
                    raise ValidationError("NetBox result rows must be objects")
                records.append(row)
                if len(records) > self.max_records:
                    raise ValidationError("NetBox synchronization exceeded the record safety limit")
            next_url = payload.get("next")
            if not next_url:
                return records
            if not isinstance(next_url, str):
                raise ValidationError("NetBox next link must be text")
            url = self._safe_next(next_url)
        raise ValidationError("NetBox synchronization exceeded the page safety limit")


class NetBoxSyncService:
    LOCAL_TYPES = {"device", "port", "cable", "rack", "location"}

    def __init__(self, session: Session, principal: Principal):
        self.db = session
        self.principal = principal
        self.tenant_id = principal.tenant_id
        if session.info.get("bypass_tenant"):
            raise AuthorizationError("NetBox synchronization cannot use a platform bypass session")
        if session.info.get("tenant_id") != self.tenant_id:
            raise AuthorizationError("NetBox synchronization requires a matching tenant session")

    def _authorize(self, permission: str, project_id: uuid.UUID, location_id: uuid.UUID):
        tenant = self.db.scalar(
            select(Tenant).where(Tenant.id == self.tenant_id, Tenant.active.is_(True))
        )
        if tenant is None:
            raise AuthorizationError("Tenant is inactive or unavailable")
        project = self.db.scalar(
            select(Project).where(
                Project.id == project_id,
                Project.tenant_id == self.tenant_id,
                Project.deleted_at.is_(None),
            )
        )
        location = self.db.scalar(
            select(Location).where(
                Location.id == location_id,
                Location.tenant_id == self.tenant_id,
                Location.deleted_at.is_(None),
            )
        )
        if project is None or location is None:
            raise NotFoundError("NetBox sync scope not found")
        actual = resolve_principal(
            self.db,
            actor_id=self.principal.actor_id,
            tenant_id=self.tenant_id,
            project_id=project_id,
            location_id=location_id,
            request_id=self.principal.request_id,
            ip_address=self.principal.ip_address,
            user_agent=self.principal.user_agent,
        )
        require_permission(actual, permission)
        return actual

    @staticmethod
    def _checksum(row: dict[str, Any]) -> str:
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _mapping(cls, row: dict[str, Any]):
        custom = row.get("custom_fields")
        if not isinstance(custom, dict):
            return None
        local_type = custom.get("sim_local_type")
        raw_local_id = custom.get("sim_local_id")
        if local_type not in cls.LOCAL_TYPES or not raw_local_id:
            return None
        try:
            local_id = uuid.UUID(str(raw_local_id))
        except ValueError:
            raise ValidationError("NetBox sim_local_id must be a UUID") from None
        return local_type, local_id

    def sync(
        self,
        *,
        name: str,
        resource: str,
        base_url: str,
        project_id: uuid.UUID,
        location_id: uuid.UUID,
        client: NetBoxClient,
        updated_after: str | None = None,
    ) -> dict[str, Any]:
        actual = self._authorize("integration:write", project_id, location_id)
        clean_name = name.strip() if isinstance(name, str) else ""
        if not clean_name or len(clean_name) > 180:
            raise ValidationError("NetBox sync name must contain 1 to 180 characters")
        cursor = self.db.scalar(
            select(NetBoxSyncCursor).where(
                NetBoxSyncCursor.tenant_id == self.tenant_id,
                NetBoxSyncCursor.name == clean_name,
                NetBoxSyncCursor.deleted_at.is_(None),
            )
        )
        now = datetime.now(UTC)
        if cursor is None:
            cursor = NetBoxSyncCursor(
                tenant_id=self.tenant_id,
                name=clean_name,
                resource=resource,
                base_url=base_url.rstrip("/"),
                status="idle",
            )
            self.db.add(cursor); self.db.flush()
        elif cursor.resource != resource or cursor.base_url.rstrip("/") != base_url.rstrip("/"):
            raise ValidationError("Existing NetBox cursor cannot change resource or origin")
        cursor.status = "running"
        cursor.last_started_at = now
        cursor.last_error = None
        self.db.flush()

        params: dict[str, Any] = {}
        effective_cursor = updated_after or cursor.cursor_value
        if effective_cursor:
            params["last_updated__gte"] = effective_cursor
        try:
            records = client.fetch_all(resource, params)
            mapped = 0
            skipped = 0
            latest = effective_cursor
            for row in records:
                identity = row.get("id")
                if identity is None:
                    raise ValidationError("NetBox row is missing id")
                mapping = self._mapping(row)
                if mapping is None:
                    skipped += 1
                    continue
                local_type, local_id = mapping
                external_id = str(identity)
                existing = self.db.scalar(
                    select(ExternalObjectMap).where(
                        ExternalObjectMap.tenant_id == self.tenant_id,
                        ExternalObjectMap.adapter == "netbox",
                        ExternalObjectMap.external_type == resource,
                        ExternalObjectMap.external_id == external_id,
                        ExternalObjectMap.deleted_at.is_(None),
                    )
                )
                checksum = self._checksum(row)
                version = str(row.get("last_updated") or row.get("updated") or "") or None
                metadata = {
                    "display": str(row.get("display") or row.get("name") or external_id)[:500],
                    "url": str(row.get("url") or "")[:1000],
                }
                if existing is None:
                    existing = ExternalObjectMap(
                        tenant_id=self.tenant_id,
                        adapter="netbox",
                        external_type=resource,
                        external_id=external_id,
                        local_type=local_type,
                        local_id=local_id,
                        external_version=version,
                        snapshot_checksum=checksum,
                        last_seen_at=now,
                        active=True,
                        metadata_json=metadata,
                    )
                    self.db.add(existing)
                else:
                    existing.local_type = local_type
                    existing.local_id = local_id
                    existing.external_version = version
                    existing.snapshot_checksum = checksum
                    existing.last_seen_at = now
                    existing.active = True
                    existing.metadata_json = metadata
                if version and (latest is None or version > latest):
                    latest = version
                mapped += 1
            cursor.cursor_value = latest
            cursor.status = "succeeded"
            cursor.last_succeeded_at = now
            cursor.records_seen = len(records)
            self.db.flush()
            record_audit(
                self.db,
                principal=actual,
                action="integration.netbox.synced",
                object_type="netbox_sync_cursor",
                object_id=cursor.id,
                after={
                    "resource": resource,
                    "records": len(records),
                    "mapped": mapped,
                    "skipped": skipped,
                    "cursor": latest,
                },
                project_id=project_id,
            )
            return {
                "cursor_id": str(cursor.id),
                "status": cursor.status,
                "records": len(records),
                "mapped": mapped,
                "skipped": skipped,
                "cursor": latest,
            }
        except Exception as exc:
            cursor.status = "failed"
            cursor.last_error = str(exc)[:1000]
            self.db.flush()
            raise

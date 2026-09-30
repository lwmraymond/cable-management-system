# PR #7 C6 — NetBox Adapter and Signed Retry Webhook

Date: 2026-09-09 (Asia/Taipei)  
Policy: local/manual gates only; GitHub Actions are prohibited.

## Delivered

### NetBox adapter

- HTTPS-by-default client with an explicit test-only HTTP override.
- Resource allowlist for devices, interfaces, cables, racks and locations.
- Token sent only in the Authorization header and never persisted.
- Same-origin pagination; redirecting `next` links to another host or outside the configured API root is rejected.
- Page and record safety limits.
- Bounded HTTP 429 handling with capped `Retry-After` delay.
- Incremental cursor using `last_updated__gte`.
- Tenant-owned sync cursor and external-object mapping records.
- Deterministic external snapshot SHA-256 and auditable map updates.
- NetBox custom fields `sim_local_type` and `sim_local_id` provide explicit mapping intent.
- Actual tenant/project/location authorization is re-resolved for every synchronization.

### Signed retry webhook

- Tenant-owned endpoint, outbox, delivery-attempt and inbound-receipt records.
- Canonical JSON payloads with persisted SHA-256 checksums.
- HMAC-SHA256 signature covering timestamp, event UUID and exact body bytes.
- Signing secrets are resolved from references at delivery time and are never stored in the database.
- Durable attempt history, deterministic exponential backoff with jitter, bounded attempt count and dead-letter state.
- Explicit manual dead-letter requeue; delivered events are not sent again.
- Inbound clock-skew validation, constant-time signature comparison and event-ID replay prevention.
- Strict API for sync, endpoint registration, enqueue, inspect, dispatch, requeue and inbound verification.
- Composition entrypoint: `uvicorn app.main_integrations:app`.

### Database

Frozen Alembic revision `200000000008` adds:

- `netbox_sync_cursors`
- `external_object_maps`
- `webhook_endpoints`
- `webhook_outbox`
- `webhook_delivery_attempts`
- `webhook_inbound_receipts`

All six tables are tenant-owned and receive PostgreSQL `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY` and tenant `USING`/`WITH CHECK` policies.

## Executed local validation

The exact source layout used for this checkpoint passed **16 isolated NetBox/Webhook/migration tests**:

- same-origin pagination and token header
- cross-origin pagination rejection
- bounded 429 retries
- record and resource safety limits
- cursor persistence and idempotent map update
- invalid mapping/project scope rejection
- fresh permission denial for a forged cached reader
- signature material sensitivity
- successful delivery and attempt persistence
- retry, due-time guard, dead letter and manual requeue
- stored payload checksum tamper rejection
- inbound signature, clock-skew and replay rejection
- permission denial for endpoint management and enqueue
- migration chain/freeze
- forced RLS on all six tables
- child-before-parent downgrade ordering

Python compilation also passed. The committed `scripts/verify_pr7_c6.sh` regenerates the JUnit evidence and performs 13 additional source assertions.

## Reproduce

```bash
bash scripts/verify_pr7_c6.sh
```

## Explicit boundaries

This checkpoint does not yet claim:

- Live NetBox compatibility against a specific deployed version or plugin set.
- Reconciliation/deactivation of objects absent from a full authoritative NetBox snapshot.
- Guaranteed existence/type validation for every `sim_local_id` before mapping; the current map is metadata and is not used as an authorization grant.
- Production secret-manager integration beyond the built-in `env:` resolver contract.
- Multi-worker claiming/lease/heartbeat semantics for concurrent outbox dispatchers.
- TLS certificate/egress policy validation in the target network.
- Real receiver load, long-duration retry/soak or DNS failure drills.
- Exact-current-PR full C1-C6 regression after all remote commits.

## Next node

C7 OpenTelemetry-compatible logs, metrics, traces and alert rules. Before production use, C6 should also gain a database lease/claim operation for multiple dispatch workers and live NetBox/receiver validation.

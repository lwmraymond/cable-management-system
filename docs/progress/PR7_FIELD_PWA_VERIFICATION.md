# PR #7 C5 — Camera QR and PWA Offline Queue

Date: 2026-09-09 (Asia/Taipei)  
Policy: local/manual gates only; GitHub Actions are prohibited.

## Delivered

### Camera and QR boundary

- Environment-facing camera request using `getUserMedia` with rear-camera preference.
- Native `BarcodeDetector` QR scanning when available.
- Manual payload fallback when camera permission, hardware or detector support is unavailable.
- Strict resource-type allowlist and UUID validation.
- Same-origin URL enforcement.
- QR tenant values are treated as untrusted hints and must match the active tenant context.
- Tenant authority always comes from the active authenticated request context, not from QR data.

### Durable offline mutation queue

- IndexedDB persistence with tenant/created-time and status indexes.
- One stable UUID idempotency key per queued mutation across retries.
- Ordered single-worker replay, optional dependency ordering and tenant isolation.
- Exponential backoff with jitter for network errors, 429 and 5xx responses.
- Separate handling for 401/403, 409, 422 and retryable errors.
- No automatic overwrite after conflict; the UI presents local/server data and requires discard or retry-as-new.
- Actor changes block queued operations rather than replaying them under a different identity.
- Explicit tenant queue deletion for logout/device handover procedures.

### Server-side idempotency

- Tenant-scoped `FieldMutationReceipt` persistence.
- Frozen Alembic revision `200000000007` with unique tenant/key hash and forced RLS.
- Raw idempotency keys, access tokens and credentials are never stored.
- Request identity covers method, path, query and body.
- Actor scope is hashed and included in collision checks.
- Successful bounded responses are replayed; 5xx responses are never cached.
- Same key with a changed request or actor returns 409.
- API responses and tenant data are never put in the service-worker cache.

### PWA shell

- Installable manifest, same-scope SVG icon and static shell service worker.
- Static assets use cache-first/network-refresh behavior.
- API traffic remains network-only and is delegated to the application queue.
- Composition entrypoint: `uvicorn app.main_field_pwa:app`.

## Reproduce

```bash
bash scripts/verify_pr7_c5.sh
```

The gate compiles the Python source, executes middleware/migration tests, exports JUnit, validates all JavaScript syntax, runs Camera QR/PWA source assertions and rejects any `.github/workflows` file.

## Explicit boundaries

The current checkpoint is `REMOTE_SOURCE_AND_REPRODUCIBLE_GATES_PUBLISHED_RERUN_PENDING`. It does not yet claim:

- Real camera hardware testing on iOS/Android devices.
- Lighthouse/browser installability and accessibility audits.
- OS process-eviction/background-sync behavior.
- Real OIDC refresh/logout/session-expiry interaction with queued mutations.
- Photo upload or offline binary attachment queueing.
- Multi-tab queue locking; current replay is intentionally single-page/single-worker.
- Exact remote-layout full regression through all C1-C5 suites.

## Next node

C6 NetBox Adapter and Signed Retry Webhook, while C1-C5 exact-layout gates are rerun on an environment that can check out the PR branch.

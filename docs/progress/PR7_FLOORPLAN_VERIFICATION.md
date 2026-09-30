# PR #7 C4 — Versioned 2D Floor Plan Editor

Date: 2026-09-09 (Asia/Taipei)  
Policy: local/manual gates only; GitHub Actions are prohibited.

## Delivered

- Tenant-owned `FloorPlan` head records and immutable `FloorPlanRevision` snapshots.
- Frozen Alembic revision `200000000006` with composite tenant foreign keys.
- PostgreSQL `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY` and tenant policies for both new tables.
- PostgreSQL append-only trigger for revision rows.
- Complete-document SHA-256 checksums, deterministic object ordering and schema version 1.
- Actual project/location authorization, including resource-location subtree checks.
- Optimistic concurrency using `expected_version`; stale saves, publishes and restores return 409.
- Publication points to an immutable revision; restore copies history into a new head revision.
- Strict seven-operation API for create/list/get/save/history/publish/restore.
- Composition entrypoint: `uvicorn app.main_floorplan_editor:app`.
- Static editor available from the existing legacy static mount at `/app/floorplan-editor.html`.
- Drag, zoom, grid/snap, resource placement, annotations, history, publish and restore controls.
- Conflict handling never silently retries or overwrites a newer server revision.

## Local validation evidence

The pre-publication C4 payload passed:

- **24 backend/API/migration tests**.
- Python compile checks.
- Four TypeScript/TSX source checks, strict helper typecheck and **24 UI assertions** in the original React-oriented payload.

During that run, two test defects were found and corrected before the final pass:

1. SQLite timezone deserialization was normalized before comparing timestamps.
2. OpenAPI validation counted HTTP operations rather than unique path templates.

The remotely published checkpoint uses a dependency-light static editor plus the same versioned service/API contract. It contains reproducible Python tests and `scripts/verify_pr7_c4.sh`. The exact remote layout must be rerun before C4 is promoted from `REMOTE_SOURCE_AND_GATES_PUBLISHED_RERUN_PENDING` to fully verified.

## Reproduce

```bash
bash scripts/verify_pr7_c4.sh
```

The script performs Python compilation, the complete `tests/floorplan` suite, JUnit export, JavaScript syntax validation, UI source assertions and a hard rejection of `.github/workflows`.

## Explicit boundaries

Not yet claimed:

- Full original host entrypoint integration; C4 currently uses the composition entrypoint.
- React dependency typecheck or Vite production build for the existing React application.
- Real browser pointer/zoom visual E2E and accessibility testing.
- Real PostgreSQL ordinary-role RLS and append-only trigger execution.
- Background-image upload through MinIO/S3 and ClamAV.
- Large-plan performance/load testing above the 5,000-object safety boundary.

## Next node

After an exact-layout C4 rerun, continue with Camera QR and the PWA offline mutation queue. Secure attachment storage remains an independent prerequisite for photo/background upload validation.

# PR #7 C4 — Versioned 2D Floor Plan Editor verification

Date: 2026-09-09 (Asia/Taipei)  
Policy: local/manual gates only; no GitHub Actions.

## Delivered

- tenant-owned `floor_plans` and append-only `floor_plan_revisions`;
- Alembic revision `200000000006`, following revision 005;
- composite tenant/project/location references and PostgreSQL Forced-RLS policies;
- immutable JSON document revisions with canonical SHA-256 checksums;
- optimistic plan-version concurrency for save, publish and restore;
- scoped resource validation for Location, Rack, Device and Pathway placement;
- canvas bounds, finite-number, duplicate resource, object/path and document-size limits;
- create/list/get/save/publish/history/restore API operations;
- React SVG editor with grid snapping, selection, drag movement, zoom, resource placement,
  revision save/publish/restore and explicit 409 reload handling;
- a pure TypeScript helper module and a local UI source gate.

## Executed local gates

```text
PYTHONPATH=apps/api python -m compileall -q \
  apps/api/app apps/api/migrations tests/floorplan

PYTHONPATH=apps/api pytest -q tests/floorplan \
  --junitxml=docs/progress/PR7_FLOOR_PLAN_TESTS.xml

node scripts/check_floor_plan_ui.cjs
```

Result:

```text
24 passed
PASS: 4 TypeScript/TSX syntax checks
PASS: strict Floor Plan helper typecheck
PASS: 24 helper/route assertions
```

The final rerun used the same root-router mount pattern now committed in PR #7:
`build_fiber_router()` includes `build_floorplan_router()` beside the `/fiber` router,
so the public paths remain `/api/v1/floor-plans/...` without a broad `main.py` rewrite.

## Covered behavior

- initial revision persistence and audit;
- deterministic canonical checksums;
- save, publish and restore version transitions;
- append-only revision history;
- stale-version rollback;
- unchanged-document rejection;
- schema, finite-number, bounds, duplicate object and path validation;
- actual project/location authorization;
- cross-tenant and out-of-scope reference rejection;
- read-only member and scoped contractor negative permissions;
- access-grant expiry recheck;
- strict API payloads and 403/404/409 behavior;
- migration 006 upgrade/downgrade, metadata match, PostgreSQL offline RLS SQL,
  SQLite append-only triggers and frozen migration source;
- route/model registration source gates;
- grid snap, clamping, locked objects, duplicate physical placement, zoom bounds and
  conflict UX helpers.

## Explicit verification boundary

The 24 tests ran in an isolated schema-compatible harness using the real C4 service,
router, models and migration, including the final remote router mount pattern. They are
not a complete repository checkout regression. The following remain unexecuted:

- the existing full backend suite against the exact PR head;
- revision 001 through 006 on real PostgreSQL;
- ordinary-role PostgreSQL RLS and concurrent transactions;
- full React/Ant Design dependency typecheck and Vite build;
- browser drag/zoom/restore E2E and visual/accessibility tests;
- background upload through a real MinIO/S3 and ClamAV lifecycle.

C4 is therefore recorded as
`REMOTE_SOURCE_AND_ISOLATED_GATES_PASSED_HOST_RERUN_PENDING`, not production-complete.

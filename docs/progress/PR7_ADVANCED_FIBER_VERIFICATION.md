# PR7 — Advanced Fiber topology and generic Cable Trace verification

Date: 2026-09-04 (Asia/Taipei)  
Parent checkpoint: `afe91b990a3c823c737d3756401941cfcc8b2307`  
Policy: local named gates only. No GitHub Actions or workflow files are added, enabled or invoked.

## Scope completed in this checkpoint

This checkpoint closes the locally implementable Fiber C2/C3 code slice:

1. **Normalized active endpoint occupancy**
   - `fiber_endpoint_claims` allows one active owner for each `(tenant, strand, side)`.
   - `physical_port_claims` allows one active owner for each `(tenant, port)`.
   - Existing active `FiberSpliceEnd` and `CableTermination` rows are backfilled by migration 005.
   - New cable and fiber termination writes create normalized claims in the same transaction.
   - Legacy-row fallbacks remain read-only safety checks while a damaged or partially migrated database is repaired.

2. **Fiber-to-port termination**
   - A strand A/B endpoint can terminate on an existing fiber-compatible port.
   - The service authorizes against the cable's actual project and the port device's actual location.
   - Release uses compare-and-swap versions and removes both normalized claims while retaining soft-deleted termination history.
   - A port already used by a legacy cable or another fiber termination is rejected.

3. **Copper Pair and Pair/Strand Channel aggregation**
   - Copper pairs are initialized from `Cable.pair_count`, with 1–600 bounds and one durable row per pair.
   - A Channel contains either FiberStrand members or CopperPair members in one project and medium.
   - Active physical resources cannot belong to two Channels simultaneously.
   - `simplex`, `duplex` and `quad` enforce 1, 2 and 4 members respectively; bundle/Ethernet remain variable within the safety bound.
   - Channel release soft-deletes the channel and members, making resources reusable.

4. **Breakout / fan-in / fan-out continuity**
   - Breakouts are attached to an actual device and project.
   - Each leg connects two explicit strand endpoints with direction, label and insertion loss.
   - Claims prevent overlap with splices, terminations and other breakout legs.
   - Legs are inserted incrementally inside one transaction so later legs see earlier same-request topology; a detected cycle rolls back the complete breakout.
   - Release preserves the breakout and leg history while releasing endpoint claims.

5. **OTDR records and topology linkage**
   - Records retain project, cable, optional strand, direction, wavelength, timezone-aware acquisition time, source identity, optional object key, total distance/loss and metadata.
   - Events retain ordered distance, type, loss, reflectance, confidence and notes.
   - Event distance must be monotonically increasing and may not exceed a declared trace length.
   - Events can be linked with optimistic versions to a splice, fiber termination or breakout leg in the same project.

6. **Generic copper/fiber Cable Trace**
   - One bounded graph traverses fiber strands, splices, breakout legs, fiber terminations, physical ports, patch-panel mappings and legacy cables.
   - Fiber traces select an explicit strand number; copper traces can select a provisioned pair number.
   - Channel membership and OTDR records are included with the selected resource.
   - Results report cycle, branching and truncation rather than silently presenting an unsafe path as complete.
   - The graph is capped at 2–1000 nodes and prunes frontier expansion before returning more than the requested cap.

7. **React advanced workbench**
   - `/app-next/fiber-topology` exposes generic trace, strand termination/release, pair provisioning, Channel create/load/trace/release, Breakout create/load/release, and OTDR create/load/link operations.
   - Text-area parsers validate member, breakout-leg and OTDR-event batch syntax before requests are sent.
   - Loaded state is reset when tenant/project/location context changes.

## New persistent tables

Migration `200000000005_advanced_fiber_topology.py` adds ten tenant-owned tables:

- `fiber_endpoint_claims`
- `physical_port_claims`
- `fiber_port_terminations`
- `copper_pairs`
- `connectivity_channels`
- `channel_members`
- `fiber_breakouts`
- `fiber_breakout_legs`
- `otdr_records`
- `otdr_events`

Every parent relation that carries tenant-owned data uses a composite `(tenant_id, id)` foreign key. PostgreSQL migration output enables and forces RLS and installs a tenant `USING`/`WITH CHECK` policy on all ten tables. Migration 002 is not rewritten to reference tables that did not exist at revision 002.

## API additions

The advanced router adds fifteen `/api/v1/fiber` paths using the application's existing database and identity dependencies:

- `POST /terminations`
- `POST /terminations/{termination_id}/release`
- `POST /cables/{cable_id}/pairs/provision`
- `GET /cables/{cable_id}/pairs`
- `POST /channels`
- `GET /channels/{channel_id}`
- `POST /channels/{channel_id}/release`
- `GET /channels/{channel_id}/trace`
- `POST /breakouts`
- `GET /breakouts/{breakout_id}`
- `POST /breakouts/{breakout_id}/release`
- `POST /otdr-records`
- `GET /otdr-records/{record_id}`
- `POST /otdr-events/{event_id}/link`
- `GET /cables/{cable_id}/trace`

The existing core `GET /api/v1/cables/{cable_id}/trace` is also routed through the generic service, while preserving the legacy copper output fields.

## Executed local gates

```bash
PYTHONPATH=apps/api python -m compileall -q \
  apps/api/app apps/api/migrations tests/fiber

PYTHONPATH=apps/api pytest -q tests/fiber \
  --junitxml=docs/progress/PR7_ADVANCED_FIBER_TESTS.xml

node scripts/check_fiber_ui.cjs
node scripts/check_fiber_topology_ui.cjs
```

| Test file | Cases |
|---|---:|
| `test_fiber.py` | 34 |
| `test_fiber_api.py` | 5 |
| `test_fiber_migration.py` | 5 |
| `test_fiber_advanced.py` | 10 |
| `test_fiber_advanced_api.py` | 5 |
| `test_fiber_advanced_migration.py` | 6 |
| `test_topology_trace.py` | 6 |
| **Total** | **71 passed** |

The 71 tests cover old Fiber regressions plus new endpoint/port exclusivity, stale versions, release/reuse, cross-tenant composite FKs, actual project/location authorization, Channel resource exclusivity and cardinality, same-request Breakout cycles, OTDR ordering/linking/timezone constraints, copper/fiber generic trace, Channel trace, graph bounds, API validation and migration roundtrip/backfill.

Frontend source gates passed:

- Foundation: 4 TypeScript/TSX syntax checks, one strict helper typecheck, 24 assertions.
- Advanced workbench: 6 TypeScript/TSX syntax checks, one strict helper typecheck, 18 assertions.

Python `compileall` passed. A trailing-whitespace gate passed on the checkpoint source. Ruff/Black/Flake8/Mypy/Pylint were not installed, and the environment could not reach the package registry, so no result is claimed for those tools.

## Test-environment boundary

The shell still cannot clone or download the complete GitHub worktree. The local run used the exact PR #7 extension files plus a schema-compatible isolated core-model/security harness sufficient for real SQLAlchemy, SQLite, router and migration behavior. It is stronger than pure mocks, but it is **not** the complete host application regression suite.

The following remain unexecuted:

- complete existing backend test suite against the full repository checkout;
- full Alembic chain from revision 001 through 005;
- real PostgreSQL ordinary-role RLS, concurrent locking and query-plan validation;
- full React dependency typecheck, Vite production build and browser E2E;
- real Keycloak, object storage, ClamAV and recovery environments.

No result in this document should be read as one of those external proofs.

## Migration and rollback

Apply revision 005 with the migration/owner role only after taking a database backup and testing a copy. Ordinary application roles need DML grants on the ten new tables, but must not receive table ownership, `SUPERUSER` or `BYPASSRLS` merely to make validation pass.

Migration 005 backfills only active legacy terminations and splice ends. It fails rather than silently choosing a winner when pre-existing data violates normalized endpoint exclusivity.

Downgrading to revision 004 removes all ten advanced tables and therefore destroys Pair, Channel, Breakout, OTDR and fiber-port termination data. Existing legacy CableTermination and revision-004 splice rows remain. Production downgrade requires an approved backup/restore plan; no destructive production drill was executed here.

## Next node

Fiber C2/C3 implementation and its named local gates are complete. The next development node is **2D Floor Plan Editor**: versioned floor-plan persistence, object placement, path geometry, optimistic concurrency and an interactive React editor. Fiber still requires full-host and real-service validation before final production acceptance, but that validation no longer blocks development of the next node.

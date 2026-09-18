#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${SIM_DRY_RUN_ID:-$$}"

if [[ -n "${SIM_DRY_RUN_DB:-}" ]]; then
  DB_PATH="$SIM_DRY_RUN_DB"
elif [[ -d /dev/shm && -w /dev/shm ]]; then
  DB_PATH="/dev/shm/sim-dry-run-${RUN_ID}.db"
else
  DB_PATH="${TMPDIR:-/tmp}/sim-dry-run-${RUN_ID}.db"
fi

SEED_1="${TMPDIR:-/tmp}/sim-dry-run-seed-${RUN_ID}-1.txt"
SEED_2="${TMPDIR:-/tmp}/sim-dry-run-seed-${RUN_ID}-2.txt"
DB_URL="sqlite+pysqlite:///${DB_PATH}"

cleanup() { rm -f "$DB_PATH" "$SEED_1" "$SEED_2"; }
trap cleanup EXIT
cleanup
printf 'dry_run_database=%s\n' "$DB_PATH"

cd "$ROOT"
PYTHONPATH=apps/api:. python -m pytest -q
cd "$ROOT/apps/api"
PYTHONPATH=. python -m compileall -q app migrations
cd "$ROOT"
node --check apps/web/app.js
node --check apps/web/webgl-viewer.js
./scripts/check-web-react-source.sh

cd "$ROOT/apps/api"
export DATABASE_URL="$DB_URL"
export PLATFORM_DATABASE_URL="$DB_URL"
export MIGRATION_DATABASE_URL="$DB_URL"
export DEMO_MODE=true
PYTHONPATH=. alembic upgrade head
PYTHONPATH=. python -m app.seed > "$SEED_1"
PYTHONPATH=. python -m app.seed > "$SEED_2"
PYTHONPATH=. python ../../scripts/check-seed-idempotency.py "$SEED_1" "$SEED_2"
PYTHONPATH=. python ../../scripts/seeded-export-smoke.py

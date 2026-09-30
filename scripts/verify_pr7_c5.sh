#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if find .github/workflows -type f -print -quit 2>/dev/null | grep -q .; then
  echo "ERROR: GitHub Actions workflows are prohibited for PR #7" >&2
  exit 1
fi

export PYTHONPATH="${PYTHONPATH:-}:apps/api"
python -m compileall -q \
  apps/api/app/field_models.py \
  apps/api/app/field_idempotency.py \
  apps/api/app/main_field_pwa.py \
  apps/api/migrations/versions/200000000007_field_mutation_receipts.py \
  tests/field
pytest -q tests/field --junitxml=docs/progress/PR7_FIELD_PWA_TESTS.xml
node --check apps/web/field/app.js
node --check apps/web/field/offline-queue.js
node --check apps/web/field/sw.js
node scripts/check_field_pwa.mjs

echo "PASS: PR #7 C5 source and isolated local gates"
echo "NOT EXECUTED: camera hardware, installability audit, browser process-eviction replay, real OIDC session expiry"

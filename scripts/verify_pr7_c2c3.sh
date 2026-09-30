#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if find .github/workflows -type f -print -quit 2>/dev/null | grep -q .; then
  echo "ERROR: GitHub Actions workflows are forbidden for PR #7" >&2
  exit 2
fi

export PYTHONPATH="${PYTHONPATH:-}:apps/api"

python -m compileall -q apps/api/app apps/api/migrations tests/fiber
pytest -q tests/fiber --junitxml=docs/progress/PR7_ADVANCED_FIBER_TESTS.xml
node scripts/check_fiber_ui.cjs
node scripts/check_fiber_topology_ui.cjs

python - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET

report = Path("docs/progress/PR7_ADVANCED_FIBER_TESTS.xml")
root = ET.parse(report).getroot()
attrs = root.attrib if root.tag == "testsuite" else next(iter(root)).attrib
failures = int(attrs.get("failures", 0))
errors = int(attrs.get("errors", 0))
if failures or errors:
    raise SystemExit(f"JUnit gate failed: failures={failures} errors={errors}")
print(
    "PASS: PR7 C2/C3 local gates; "
    f"tests={attrs.get('tests', 'unknown')} failures={failures} errors={errors}"
)
PY

echo "NOT EXECUTED: real ordinary-role PostgreSQL, Keycloak browser lifecycle, MinIO/S3, ClamAV, browser E2E or PITR drill."

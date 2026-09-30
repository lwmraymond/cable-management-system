#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if find .github/workflows -type f -print -quit 2>/dev/null | grep -q .; then
  echo "ERROR: GitHub Actions workflows are forbidden for PR #7" >&2
  exit 2
fi

export PYTHONPATH="${PYTHONPATH:-}:apps/api"
python -m compileall -q apps/api/app apps/api/migrations tests/floorplan
pytest -q tests/floorplan --junitxml=docs/progress/PR7_FLOOR_PLAN_TESTS.xml
node scripts/check_floor_plan_ui.cjs

python - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET

report = Path("docs/progress/PR7_FLOOR_PLAN_TESTS.xml")
root = ET.parse(report).getroot()
suite = root if root.tag == "testsuite" else next(iter(root))
failures = int(suite.attrib.get("failures", 0))
errors = int(suite.attrib.get("errors", 0))
if failures or errors:
    raise SystemExit(f"Floor Plan JUnit gate failed: failures={failures} errors={errors}")
print(
    "PASS: PR7 C4 Floor Plan local gates; "
    f"tests={suite.attrib.get('tests', 'unknown')} failures={failures} errors={errors}"
)
PY

echo "NOT EXECUTED: complete host regression, real PostgreSQL RLS, React dependency build, browser E2E, MinIO/S3 or ClamAV."

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
  apps/api/app/integration_models.py \
  apps/api/app/services/netbox_adapter.py \
  apps/api/app/services/signed_webhooks.py \
  apps/api/app/api/integrations.py \
  apps/api/app/main_integrations.py \
  apps/api/migrations/versions/200000000008_netbox_signed_webhooks.py \
  tests/integrations

pytest -q tests/integrations --junitxml=docs/progress/PR7_INTEGRATIONS_TESTS.xml

python - <<'PY'
from pathlib import Path
checks = {
    "no workflow": not Path(".github/workflows").exists(),
    "NetBox origin guard": "pagination attempted to leave" in Path("apps/api/app/services/netbox_adapter.py").read_text(),
    "NetBox page bound": "max_pages" in Path("apps/api/app/services/netbox_adapter.py").read_text(),
    "NetBox record bound": "max_records" in Path("apps/api/app/services/netbox_adapter.py").read_text(),
    "NetBox 429 retry": "Retry-After" in Path("apps/api/app/services/netbox_adapter.py").read_text(),
    "secret reference only": "secret_reference" in Path("apps/api/app/integration_models.py").read_text(),
    "HMAC SHA256": "hmac.new" in Path("apps/api/app/services/signed_webhooks.py").read_text(),
    "timestamp signature": "timestamp" in Path("apps/api/app/services/signed_webhooks.py").read_text(),
    "event id signature": "event_id" in Path("apps/api/app/services/signed_webhooks.py").read_text(),
    "inbound replay receipt": "WebhookInboundReceipt" in Path("apps/api/app/services/signed_webhooks.py").read_text(),
    "dead letter": 'row.state = "dead"' in Path("apps/api/app/services/signed_webhooks.py").read_text(),
    "manual requeue": "requeue_dead" in Path("apps/api/app/services/signed_webhooks.py").read_text(),
    "forced RLS": "FORCE ROW LEVEL SECURITY" in Path("apps/api/migrations/versions/200000000008_netbox_signed_webhooks.py").read_text(),
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit(f"C6 source gate failed: {failed}")
print(f"PASS: {len(checks)} NetBox/Webhook source assertions")
PY

echo "PASS: PR #7 C6 isolated gate"
echo "NOT EXECUTED: live NetBox, production receiver, secret manager, multi-worker outbox claim/lease"

.PHONY: test verify dry-run postgres-rls runtime-shared-rate-limit seed run

test:
	PYTHONPATH=apps/api:. python -m pytest -q

verify: test
	cd apps/api && PYTHONPATH=. python -m compileall -q app migrations
	node --check apps/web/app.js
	node --check apps/web/webgl-viewer.js
	./scripts/check-web-react-source.sh

dry-run:
	./scripts/dry-run.sh

postgres-rls:
	PYTHONPATH=apps/api:. python scripts/postgres_rls_attack_matrix.py

runtime-shared-rate-limit:
	PYTHONPATH=apps/api:. python scripts/runtime_shared_rate_limit_smoke.py

seed:
	cd apps/api && PYTHONPATH=. python -m app.seed

run:
	cd apps/api && PYTHONPATH=. uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

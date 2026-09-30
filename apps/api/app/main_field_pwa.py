"""PR #7 field/PWA application composition entrypoint.

Run with:
    uvicorn app.main_field_pwa:app

This composes the existing host, C4 Floor Plan routes and the tenant-scoped
idempotency middleware required by offline mutation replay.
"""
from app.field_idempotency import FieldIdempotencyMiddleware
from app.main_floorplan_editor import app

if not any(middleware.cls is FieldIdempotencyMiddleware for middleware in app.user_middleware):
    app.add_middleware(FieldIdempotencyMiddleware)

"""Application entrypoint with the PR #7 floor-plan editor router mounted.

Run with:
    uvicorn app.main_floorplan_editor:app

Keeping this as a composition entrypoint avoids rewriting the large existing
host module while the C4 checkpoint remains in draft review. The normal host app
is imported first, so all existing middleware and C1-C3 routes remain active.
"""
from app.api.deps import get_db, get_principal
from app.api.floorplan_editor import build_floorplan_router
from app.config import get_settings
from app.main import app

settings = get_settings()

if not any(getattr(route, "path", "").startswith(f"{settings.api_prefix}/floor-plans") for route in app.routes):
    app.include_router(
        build_floorplan_router(get_db, get_principal),
        prefix=settings.api_prefix,
    )

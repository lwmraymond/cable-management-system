"""PR #7 application composition through C6 integrations.

Run with:
    uvicorn app.main_integrations:app
"""
from app.api.deps import get_db, get_principal
from app.api.integrations import build_integrations_router
from app.config import get_settings
from app.main_field_pwa import app

settings = get_settings()
if not any(getattr(route, "path", "").startswith(f"{settings.api_prefix}/integrations") for route in app.routes):
    app.include_router(
        build_integrations_router(get_db, get_principal),
        prefix=settings.api_prefix,
    )

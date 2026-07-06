"""Cache wiring + CORS guards.

Finding 1: the run/settings cache stores use the Supabase service role, which
bypasses RLS; the route-layer org-membership check is then the ONLY tenant
boundary and it no-ops when auth is off. So the stores must NEVER be configured
unless auth is required — even when supabase_url + service_role_key are set.

Finding 2: cross-origin cache-settings PATCH edits must survive the browser
preflight, so PATCH must be in the CORS allowed methods.
"""

from app.config import Settings
from app.main import _configure_dashboard_cache, app
from app.services.dashboard_cache_settings import (
    configure_cache_settings_store,
    get_cache_settings_store,
)
from app.services.dashboard_run_cache import (
    configure_run_cache_store,
    get_run_cache_store,
)


def _reset_stores() -> None:
    configure_cache_settings_store(None)
    configure_run_cache_store(None)


def test_cache_stores_not_configured_when_auth_disabled() -> None:
    # auth off + full Supabase creds present: stores must stay None so no
    # unauthenticated cross-org read is possible.
    settings = Settings(
        auth_required=False,
        supabase_url="https://project.supabase.co",
        supabase_service_role_key="service-role-key",
    )
    _reset_stores()
    try:
        _configure_dashboard_cache(settings)
        assert get_run_cache_store() is None
        assert get_cache_settings_store() is None
    finally:
        _reset_stores()


def test_cache_stores_configured_when_auth_required() -> None:
    # auth on + Supabase creds: the stores ARE wired (existing behavior intact).
    settings = Settings(
        auth_required=True,
        supabase_url="https://project.supabase.co",
        supabase_service_role_key="service-role-key",
    )
    _reset_stores()
    try:
        _configure_dashboard_cache(settings)
        assert get_run_cache_store() is not None
        assert get_cache_settings_store() is not None
    finally:
        _reset_stores()


def test_cors_allows_patch_method() -> None:
    from fastapi.middleware.cors import CORSMiddleware

    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert "PATCH" in cors.kwargs["allow_methods"]

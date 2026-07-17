import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app import auth
from app.config import Settings
from app.routes.automated_savings import router as automated_savings_router
from app.routes.dashboard_runs import router as dashboard_runs_router
from app.routes.health import router as health_router
from app.routes.onboarding import router as onboarding_router
from app.routes.organizations import router as organizations_router
from app.routes.session import router as session_router
from app.routes.snowflake import router as snowflake_router
from app.services import query_concurrency
from app.services.http_pool import (
    HTTP_LIMITS,
    clear_clients,
    client_timeout,
    install_clients,
)
from app.services.automated_savings_store import (
    SupabaseAutomatedSavingsStore,
    configure_automated_savings_store,
)
from app.services.org_invitations import (
    SupabaseMemberRpc,
    SupabaseUserInviter,
    configure_invitations,
)
from app.services.dashboard_cache_settings import (
    SupabaseCacheSettingsStore,
    configure_cache_settings_store,
)
from app.services.dashboard_run_cache import (
    SupabaseRunCacheStore,
    configure_run_cache_store,
)
from app.services.org_provisioning import (
    SupabaseOrgDisconnector,
    SupabaseOrgProvisioner,
    configure_org_disconnector,
    configure_org_provisioner,
)

logger = logging.getLogger(__name__)


def _configure_org_provisioner(settings: Settings) -> None:
    if settings.supabase_url.strip() and settings.supabase_service_role_key.strip():
        configure_org_provisioner(
            SupabaseOrgProvisioner(
                supabase_url=settings.supabase_url,
                service_role_key=settings.supabase_service_role_key,
            )
        )
    else:
        configure_org_provisioner(None)


def _configure_org_disconnector(settings: Settings) -> None:
    if settings.supabase_url.strip() and settings.supabase_service_role_key.strip():
        configure_org_disconnector(
            SupabaseOrgDisconnector(
                supabase_url=settings.supabase_url,
                service_role_key=settings.supabase_service_role_key,
            )
        )
    else:
        configure_org_disconnector(None)


def _configure_invitations(settings: Settings) -> None:
    if settings.supabase_url.strip() and settings.supabase_service_role_key.strip():
        configure_invitations(
            SupabaseMemberRpc(
                supabase_url=settings.supabase_url,
                service_role_key=settings.supabase_service_role_key,
            ),
            SupabaseUserInviter(
                supabase_url=settings.supabase_url,
                service_role_key=settings.supabase_service_role_key,
            ),
        )
    else:
        configure_invitations(None, None)


def _configure_dashboard_cache(settings: Settings) -> None:
    # The cache stores use the Supabase service role, which bypasses RLS — the
    # route-layer org-membership check is then the ONLY tenant boundary. That
    # check no-ops when auth is disabled, so an auth-off deployment with cache
    # stores wired would let an unauthenticated caller read any org's cached
    # data. Never configure the service-role cache stores unless auth is
    # required: with them left as None, /cached returns 204 and /cache-settings
    # returns harmless defaults, and no cross-org read is possible.
    if (
        settings.auth_required
        and settings.supabase_url.strip()
        and settings.supabase_service_role_key.strip()
    ):
        configure_cache_settings_store(
            SupabaseCacheSettingsStore(
                supabase_url=settings.supabase_url,
                service_role_key=settings.supabase_service_role_key,
            )
        )
        configure_run_cache_store(
            SupabaseRunCacheStore(
                supabase_url=settings.supabase_url,
                service_role_key=settings.supabase_service_role_key,
            )
        )
    else:
        configure_cache_settings_store(None)
        configure_run_cache_store(None)


def _configure_automated_savings_store(settings: Settings) -> None:
    # Same rationale as _configure_dashboard_cache: this store uses the
    # Supabase service role and bypasses RLS, so the route-layer org-
    # membership check is the only tenant boundary. That check no-ops when
    # auth is disabled, so never wire the service-role store unless auth is
    # required — otherwise an auth-off deployment could read/write any org's
    # automated-savings data.
    if (
        settings.auth_required
        and settings.supabase_url.strip()
        and settings.supabase_service_role_key.strip()
    ):
        configure_automated_savings_store(
            SupabaseAutomatedSavingsStore(
                supabase_url=settings.supabase_url,
                service_role_key=settings.supabase_service_role_key,
            )
        )
    else:
        configure_automated_savings_store(None)


settings = Settings()
auth.configure_supabase_session_verifier(settings)
auth.configure_membership_lookup(settings)
_configure_org_provisioner(settings)
_configure_org_disconnector(settings)
_configure_invitations(settings)
_configure_dashboard_cache(settings)
_configure_automated_savings_store(settings)
query_concurrency.configure(settings.query_concurrency)


def warn_when_auth_required_without_verifier(settings: Settings) -> None:
    if settings.auth_required and auth.supabase_session_verifier is None:
        logger.warning(
            "AUTH_REQUIRED=true but no Supabase session verifier is configured; "
            "bearer tokens will be rejected until a verifier is registered."
        )


def require_membership_lookup_when_auth_required(settings: Settings) -> None:
    if settings.auth_required and not settings.supabase_service_role_key.strip():
        raise RuntimeError(
            "AUTH_REQUIRED=true requires SUPABASE_SERVICE_ROLE_KEY for live "
            "organization membership lookups."
        )


warn_when_auth_required_without_verifier(settings)
require_membership_lookup_when_auth_required(settings)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Own the process-wide query executor across the app lifecycle.

    Reconfigure the executor on startup so a restarted app object (same
    process) revives the singleton that the previous shutdown tore down, then
    tear it down on shutdown so queued Snowflake query work does not outlive
    the process (e.g. on reload).
    """
    auth_client = httpx.AsyncClient(limits=HTTP_LIMITS, timeout=client_timeout())
    async_client = httpx.AsyncClient(limits=HTTP_LIMITS, timeout=client_timeout())
    sync_client = httpx.Client(limits=HTTP_LIMITS, timeout=client_timeout())
    install_clients(
        auth=auth_client, async_client=async_client, sync_client=sync_client
    )
    try:
        query_concurrency.configure(settings.query_concurrency)
        yield
    finally:
        clear_clients()
        await auth_client.aclose()
        await async_client.aclose()
        sync_client.close()
        query_concurrency.shutdown(cancel_futures=True)


app = FastAPI(title="Greysight API", lifespan=_lifespan)

_SENSITIVE_FIELDS = {"private_key_pem", "passphrase"}


@app.exception_handler(RequestValidationError)
async def _redact_validation_errors(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Redact submitted secrets from 422 validation responses.

    FastAPI's default handler echoes the offending `input` in `exc.errors()`,
    which would leak a submitted `private_key_pem`/`passphrase` back to the
    client. Preserve the normal `{"detail": [...]}` shape for all other fields.
    """
    redacted = []
    for err in exc.errors():
        e = dict(err)
        loc = e.get("loc", ())
        if any(part in _SENSITIVE_FIELDS for part in loc):
            if "input" in e:
                e["input"] = "[redacted]"
            e.pop("ctx", None)
        redacted.append(e)
    return JSONResponse(status_code=422, content=jsonable_encoder({"detail": redacted}))


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_allowed_origins),
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["authorization", "content-type"],
)
app.include_router(health_router)
app.include_router(snowflake_router)
app.include_router(dashboard_runs_router)
app.include_router(session_router)
app.include_router(onboarding_router)
app.include_router(organizations_router)
app.include_router(automated_savings_router)

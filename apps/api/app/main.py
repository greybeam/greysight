import logging

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app import auth
from app.config import Settings
from app.routes.dashboard_runs import router as dashboard_runs_router
from app.routes.health import router as health_router
from app.routes.onboarding import router as onboarding_router
from app.routes.session import router as session_router
from app.routes.snowflake import router as snowflake_router
from app.services import query_concurrency
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


settings = Settings()
auth.configure_supabase_session_verifier(settings)
auth.configure_membership_lookup(settings)
_configure_org_provisioner(settings)
_configure_org_disconnector(settings)
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

app = FastAPI(title="Greysight API")

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
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["authorization", "content-type"],
)
app.include_router(health_router)
app.include_router(snowflake_router)
app.include_router(dashboard_runs_router)
app.include_router(session_router)
app.include_router(onboarding_router)

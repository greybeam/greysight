import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import auth
from app.config import Settings
from app.routes.dashboard_runs import router as dashboard_runs_router
from app.routes.health import router as health_router
from app.routes.session import router as session_router
from app.routes.snowflake import router as snowflake_router

logger = logging.getLogger(__name__)
settings = Settings()
auth.configure_supabase_session_verifier(settings)
auth.configure_membership_lookup(settings)


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

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import auth
from app.config import Settings
from app.routes.dashboard_runs import router as dashboard_runs_router
from app.routes.health import router as health_router
from app.routes.snowflake import router as snowflake_router

logger = logging.getLogger(__name__)
settings = Settings()


def warn_when_auth_required_without_verifier(settings: Settings) -> None:
    if settings.auth_required and auth.supabase_session_verifier is None:
        logger.warning(
            "AUTH_REQUIRED=true but no Supabase session verifier is configured; "
            "bearer tokens will be rejected until a verifier is registered."
        )


warn_when_auth_required_without_verifier(settings)

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

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.config import Settings
from app.services.audit_events import audit_event_recorder
from app.services.snowflake_client import (
    SnowflakeConfigurationError,
    SnowflakeValidationError,
    validate_snowflake_connection,
)

router = APIRouter(prefix="/api/snowflake", tags=["snowflake"])


class SnowflakeValidationResponse(BaseModel):
    status: str
    message: str


@router.post("/validate", response_model=SnowflakeValidationResponse)
def validate_snowflake() -> SnowflakeValidationResponse:
    # Self-host/dev convenience only: this route validates against the
    # deployment .env Snowflake credentials. Under multi-tenant auth, per-org
    # validation happens inside POST /api/onboarding/connect against
    # user-supplied creds. Gate the route to auth-off deployments and make it
    # uniformly invisible (404) otherwise, before any auth dependency runs.
    if Settings().auth_required:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    try:
        validate_snowflake_connection()
    except SnowflakeConfigurationError:
        audit_event_recorder.record_event(
            "snowflake.validation_attempted",
            payload={"outcome": "configuration_error"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Snowflake connection is not configured.",
        ) from None
    except SnowflakeValidationError:
        audit_event_recorder.record_event(
            "snowflake.validation_attempted",
            payload={"outcome": "failed"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate Snowflake Account Usage access.",
        ) from None
    except Exception:
        audit_event_recorder.record_event(
            "snowflake.validation_attempted",
            payload={"outcome": "error"},
        )
        raise
    audit_event_recorder.record_event(
        "snowflake.validation_attempted",
        payload={"outcome": "succeeded"},
    )
    return SnowflakeValidationResponse(
        status="ok", message="Snowflake access validated."
    )

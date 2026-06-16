from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import AuthContext, require_auth_context
from app.services.snowflake_account import (
    InvalidSnowflakeAccountError,
    validate_account_identifier,
)
from app.services.snowflake_client import (
    SnowflakeConfigurationError,
    SnowflakeConnectionConfig,
    SnowflakeValidationError,
    validate_snowflake_connection,
)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

MAX_PEM_BYTES = 16 * 1024
VALIDATION_TIMEOUT_SECONDS = 20


class ConnectRequest(BaseModel):
    org_name: str = Field(min_length=1, max_length=200)
    account: str = Field(min_length=1, max_length=255)
    user: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=255)
    warehouse: str = Field(min_length=1, max_length=255)
    database: str | None = Field(default=None, max_length=255)
    schema: str | None = Field(default=None, max_length=255)
    private_key_pem: str = Field(min_length=1)
    passphrase: str | None = Field(default=None, max_length=1024)


class ConnectResponse(BaseModel):
    id: str


def create_org_with_connection(**kwargs: object) -> str:
    """Indirection seam so tests can stub the service-role RPC call."""
    from app.services.org_provisioning import create_org_with_connection as impl

    return impl(**kwargs)


@router.post(
    "/connect", response_model=ConnectResponse, status_code=status.HTTP_201_CREATED
)
def connect_snowflake(
    request: ConnectRequest,
    auth_context: AuthContext = Depends(require_auth_context),
) -> ConnectResponse:
    if not auth_context.auth_required or not auth_context.user_id:
        raise HTTPException(status_code=403, detail="Authentication required")

    if len(request.private_key_pem.encode("utf-8")) > MAX_PEM_BYTES:
        raise HTTPException(status_code=422, detail="Private key is too large.")

    from app.services.connect_rate_limit import (
        ConnectInFlightError,
        ConnectRateLimitedError,
        get_connect_limiter,
    )

    try:
        with get_connect_limiter().guard(auth_context.user_id):
            return _validate_and_create(request, auth_context)
    except ConnectInFlightError:
        raise HTTPException(
            status_code=409, detail="A connection attempt is already in progress."
        ) from None
    except ConnectRateLimitedError:
        raise HTTPException(
            status_code=429, detail="Too many connection attempts. Try again shortly."
        ) from None


def _validate_and_create(
    request: ConnectRequest, auth_context: AuthContext
) -> ConnectResponse:
    try:
        account = validate_account_identifier(request.account)
    except InvalidSnowflakeAccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    config = SnowflakeConnectionConfig(
        account=account,
        user=request.user,
        role=request.role,
        warehouse=request.warehouse,
        database=request.database,
        schema=request.schema,
        private_key_pem=request.private_key_pem,
        private_key_passphrase=request.passphrase,
        query_timeout_seconds=VALIDATION_TIMEOUT_SECONDS,
    )

    try:
        validate_snowflake_connection(config)
    except SnowflakeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except SnowflakeConfigurationError:
        # Most common onboarding error: a malformed PEM or wrong passphrase.
        # Surface a neutral 422, never a 500 (the message is already generic and
        # leaks no key material).
        raise HTTPException(
            status_code=422,
            detail="Snowflake private key could not be loaded. Check the PEM and passphrase.",
        ) from None

    from app.services.org_provisioning import (
        OrgAlreadyExistsError,
        OrgProvisioningError,
    )

    try:
        organization_id = create_org_with_connection(
            p_user_id=auth_context.user_id,
            p_org_name=request.org_name,
            p_account=account,
            p_user=request.user,
            p_role=request.role,
            p_warehouse=request.warehouse,
            p_database=request.database or "",
            p_schema=request.schema or "",
            p_private_key_pem=request.private_key_pem,
            p_passphrase=request.passphrase or "",
        )
    except OrgAlreadyExistsError:
        raise HTTPException(
            status_code=409, detail="You already have an organization."
        ) from None
    except OrgProvisioningError:
        raise HTTPException(
            status_code=502, detail="Could not create the organization."
        ) from None
    return ConnectResponse(id=str(organization_id))

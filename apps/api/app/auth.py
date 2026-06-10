from dataclasses import dataclass, field
from typing import Annotated
from typing import Collection

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

DEMO_ORGANIZATION_ID = "demo-org"
_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    user_id: str | None
    auth_required: bool
    memberships: Collection[str] = field(default_factory=frozenset)


def require_org_membership(
    context: AuthContext,
    organization_id: str,
    *,
    allow_demo: bool = False,
) -> None:
    if (
        not context.auth_required
        and allow_demo
        and organization_id == DEMO_ORGANIZATION_ID
    ):
        return None

    if organization_id in context.memberships:
        return None

    raise HTTPException(status_code=403, detail="Organization access denied")


async def require_auth_context(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ] = None,
) -> AuthContext:
    settings = Settings()
    if not settings.auth_required:
        return AuthContext(
            user_id=None,
            auth_required=False,
            memberships=frozenset(),
        )

    if credentials is None or not credentials.credentials.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await validate_supabase_session(credentials.credentials)


async def validate_supabase_session(token: str) -> AuthContext:
    if not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthContext(
        user_id="authenticated",
        auth_required=True,
        memberships=frozenset(),
    )

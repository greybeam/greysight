import inspect
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

DEMO_ORGANIZATION_ID = "demo-org"
_bearer_scheme = HTTPBearer(auto_error=False)
SupabaseSessionVerifier = Callable[
    [str], Mapping[str, object] | Awaitable[Mapping[str, object]]
]
supabase_session_verifier: SupabaseSessionVerifier | None = None


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
    stripped_token = token.strip()
    if not stripped_token:
        raise _authentication_required()

    if supabase_session_verifier is None:
        raise _authentication_required()

    claims_result = supabase_session_verifier(stripped_token)
    claims = (
        await claims_result if inspect.isawaitable(claims_result) else claims_result
    )
    if not isinstance(claims, Mapping):
        raise _authentication_required()

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise _authentication_required()

    return AuthContext(
        user_id=user_id.strip(),
        auth_required=True,
        memberships=_extract_memberships(claims),
    )


def _extract_memberships(claims: Mapping[str, object]) -> frozenset[str]:
    memberships: set[str] = set()
    app_metadata = claims.get("app_metadata")

    if isinstance(app_metadata, Mapping):
        memberships.update(_string_list_claim(app_metadata.get("organization_ids")))
        memberships.update(_string_list_claim(app_metadata.get("organizations")))

    memberships.update(_string_list_claim(claims.get("memberships")))
    return frozenset(memberships)


def _string_list_claim(value: object) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return frozenset()

    return frozenset(item.strip() for item in value if item.strip())


def _authentication_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )

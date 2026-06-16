import inspect
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.services.membership_directory import (
    MembershipLookup,
    MembershipLookupError,
    Organization,
    SupabaseServiceRoleMembershipLookup,
)

DEMO_ORGANIZATION_ID = "demo-org"
_bearer_scheme = HTTPBearer(auto_error=False)
SupabaseSessionVerifier = Callable[
    [str], Mapping[str, object] | Awaitable[Mapping[str, object]]
]
supabase_session_verifier: SupabaseSessionVerifier | None = None
membership_lookup: MembershipLookup | None = None


class SupabaseAuthServerVerifier:
    def __init__(
        self,
        *,
        supabase_url: str,
        supabase_anon_key: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._user_url = f"{supabase_url.rstrip('/')}/auth/v1/user"
        self._anon_key = supabase_anon_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def __call__(self, token: str) -> Mapping[str, object]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    self._user_url,
                    headers={
                        "apikey": self._anon_key,
                        "authorization": f"Bearer {token}",
                    },
                )
        except httpx.HTTPError as exc:
            raise _authentication_required() from exc
        if response.status_code != status.HTTP_200_OK:
            raise _authentication_required()

        try:
            payload = response.json()
        except ValueError as exc:
            raise _authentication_required() from exc
        if not isinstance(payload, Mapping):
            raise _authentication_required()

        user_id = payload.get("id") or payload.get("sub")
        return {
            "sub": user_id,
            "app_metadata": payload.get("app_metadata", {}),
        }


def configure_supabase_session_verifier(settings: Settings) -> None:
    global supabase_session_verifier
    if settings.supabase_url.strip() and settings.supabase_anon_key.strip():
        supabase_session_verifier = SupabaseAuthServerVerifier(
            supabase_url=settings.supabase_url,
            supabase_anon_key=settings.supabase_anon_key,
        )
    else:
        supabase_session_verifier = None


def configure_membership_lookup(settings: Settings) -> None:
    global membership_lookup
    if settings.supabase_url.strip() and settings.supabase_service_role_key.strip():
        membership_lookup = SupabaseServiceRoleMembershipLookup(
            supabase_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
        )
    else:
        membership_lookup = None


@dataclass(frozen=True)
class AuthContext:
    user_id: str | None
    auth_required: bool
    memberships: Collection[str] = field(default_factory=frozenset)
    organizations: Collection[Organization] = field(default_factory=tuple)


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

    if _normalize_membership_id(organization_id) in context.memberships:
        return None

    raise HTTPException(status_code=403, detail="Organization access denied")


def require_org_admin(context: AuthContext, organization_id: str) -> None:
    normalized = _normalize_membership_id(organization_id)
    for org in context.organizations:
        if _normalize_membership_id(org.id) == normalized and org.role in (
            "owner",
            "admin",
        ):
            return None
    raise HTTPException(status_code=403, detail="Organization admin access required")


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


async def validate_supabase_session(
    token: str,
    verifier: SupabaseSessionVerifier | None = None,
    lookup: MembershipLookup | None = None,
) -> AuthContext:
    stripped_token = token.strip()
    if not stripped_token:
        raise _authentication_required()

    selected_verifier = verifier or supabase_session_verifier
    if selected_verifier is None:
        raise _authentication_required()

    claims_result = selected_verifier(stripped_token)
    claims = (
        await claims_result if inspect.isawaitable(claims_result) else claims_result
    )
    if not isinstance(claims, Mapping):
        raise _authentication_required()

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise _authentication_required()

    normalized_user_id = user_id.strip()
    organizations = await _fetch_organizations(normalized_user_id, lookup)

    return AuthContext(
        user_id=normalized_user_id,
        auth_required=True,
        memberships=frozenset(
            _normalize_membership_id(org.id) for org in organizations
        ),
        organizations=organizations,
    )


async def _fetch_organizations(
    user_id: str,
    lookup: MembershipLookup | None,
) -> tuple[Organization, ...]:
    selected_lookup = lookup if lookup is not None else membership_lookup
    if selected_lookup is None:
        return ()
    try:
        lookup_result = selected_lookup(user_id)
        organizations = (
            await lookup_result if inspect.isawaitable(lookup_result) else lookup_result
        )
    except MembershipLookupError as exc:
        raise _authentication_required() from exc
    return tuple(organizations)


def _normalize_membership_id(value: str) -> str:
    stripped_value = value.strip()
    if not stripped_value:
        return ""
    try:
        return str(UUID(stripped_value))
    except ValueError:
        return stripped_value.lower()


def _authentication_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )

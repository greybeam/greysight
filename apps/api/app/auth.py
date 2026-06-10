import inspect
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

DEMO_ORGANIZATION_ID = "demo-org"
_bearer_scheme = HTTPBearer(auto_error=False)
SupabaseSessionVerifier = Callable[
    [str], Mapping[str, object] | Awaitable[Mapping[str, object]]
]
supabase_session_verifier: SupabaseSessionVerifier | None = None


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

    if _normalize_membership_id(organization_id) in context.memberships:
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


async def validate_supabase_session(
    token: str,
    verifier: SupabaseSessionVerifier | None = None,
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
    if not isinstance(value, list):
        return frozenset()

    items: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        normalized_item = _normalize_membership_id(item)
        if normalized_item:
            items.add(normalized_item)
    return frozenset(items)


def _normalize_membership_id(value: str) -> str:
    stripped_value = value.strip()
    if not stripped_value:
        return ""
    try:
        return str(UUID(stripped_value))
    except ValueError:
        return stripped_value


def _authentication_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )

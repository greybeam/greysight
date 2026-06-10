from dataclasses import dataclass, field
from typing import Collection

from fastapi import HTTPException

DEMO_ORGANIZATION_ID = "demo-org"


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


async def validate_supabase_session(token: str) -> AuthContext:
    del token
    raise NotImplementedError("Supabase session validation is not implemented")

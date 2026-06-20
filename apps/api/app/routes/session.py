from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import AuthContext, require_auth_context

router = APIRouter(prefix="/api/session", tags=["session"])


class SessionOrganization(BaseModel):
    id: str
    name: str
    role: str = "member"
    account_locator: str | None = None


class SessionMembershipsResponse(BaseModel):
    organizations: list[SessionOrganization]


@router.get("/memberships", response_model=SessionMembershipsResponse)
def get_session_memberships(
    context: AuthContext = Depends(require_auth_context),
) -> SessionMembershipsResponse:
    return SessionMembershipsResponse(
        organizations=[
            SessionOrganization(
                id=org.id,
                name=org.name,
                role=org.role,
                account_locator=org.account_locator,
            )
            for org in context.organizations
        ]
    )

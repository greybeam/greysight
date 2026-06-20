from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import AuthContext, require_auth_context, require_org_admin

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


class InviteRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)


class InviteResponse(BaseModel):
    email: str


def invite_member_to_org(**kwargs: object) -> str:
    """Indirection seam so tests can stub the service-role orchestration."""
    from app.services.org_invitations import invite_member_to_org as impl

    return impl(**kwargs)  # type: ignore[arg-type]


@router.post("/{organization_id}/invitations", response_model=InviteResponse)
def invite_user(
    organization_id: str,
    request: InviteRequest,
    auth_context: AuthContext = Depends(require_auth_context),
) -> InviteResponse:
    require_org_admin(auth_context, organization_id)

    from app.services.work_email import is_work_email

    email = request.email.strip()
    if not is_work_email(email):
        raise HTTPException(status_code=422, detail="Please use your work email.")

    active_org = next(
        (o for o in auth_context.organizations if o.id == organization_id),
        None,
    )

    from app.services.connect_rate_limit import (
        ConnectInFlightError,
        ConnectRateLimitedError,
        get_invite_limiter,
    )
    from app.services.org_invitations import (
        AlreadyMemberError,
        InviteProvisioningError,
        UnauthorizedInviteError,
    )

    try:
        with get_invite_limiter().guard(auth_context.user_id):
            invite_member_to_org(
                actor_user_id=auth_context.user_id,
                organization_id=organization_id,
                email=email,
                org_name=active_org.name if active_org else None,
                account_locator=active_org.account_locator if active_org else None,
            )
    except ConnectInFlightError:
        raise HTTPException(
            status_code=409, detail="An invite is already in progress."
        ) from None
    except ConnectRateLimitedError:
        raise HTTPException(
            status_code=429, detail="Too many invites. Try again shortly."
        ) from None
    except AlreadyMemberError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except UnauthorizedInviteError:
        raise HTTPException(
            status_code=403, detail="Organization admin access required"
        ) from None
    except InviteProvisioningError:
        raise HTTPException(
            status_code=502, detail="Could not send the invite."
        ) from None

    from app.services.audit_events import audit_event_recorder

    # Org-scoped audit trail for a membership-mutating action. Payload mirrors the
    # sanitized style of dashboard_runs/snowflake call sites — actor + invitee,
    # no upstream/provider detail.
    audit_event_recorder.record_org_event(
        "organization.member_invited",
        organization_id=organization_id,
        payload={"actor_user_id": auth_context.user_id, "email": email},
    )

    return InviteResponse(email=email)

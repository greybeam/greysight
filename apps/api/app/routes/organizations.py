from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import (
    AuthContext,
    require_auth_context,
    require_org_admin,
    require_org_membership,
)
from app.services.dashboard_cache_settings import (
    MAX_CACHE_TTL_SECONDS,
    MIN_CACHE_TTL_SECONDS,
    CacheSettings,
    CacheSettingsStoreError,
    get_cache_settings_store,
    read_cache_settings,
)

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


class CacheSettingsResponse(BaseModel):
    cache_enabled: bool
    cache_ttl_seconds: int


class CacheSettingsUpdateRequest(BaseModel):
    cache_enabled: bool | None = None
    cache_ttl_seconds: int | None = Field(
        default=None,
        ge=MIN_CACHE_TTL_SECONDS,
        le=MAX_CACHE_TTL_SECONDS,
    )


def _cache_settings_response(settings: CacheSettings) -> CacheSettingsResponse:
    return CacheSettingsResponse(
        cache_enabled=settings.cache_enabled,
        cache_ttl_seconds=settings.cache_ttl_seconds,
    )


@router.get("/{organization_id}/cache-settings", response_model=CacheSettingsResponse)
def read_cache_settings_route(
    organization_id: str,
    auth_context: AuthContext = Depends(require_auth_context),
) -> CacheSettingsResponse:
    if auth_context.auth_required:
        require_org_membership(auth_context, organization_id)
    settings = read_cache_settings(organization_id, get_cache_settings_store())
    return _cache_settings_response(settings)


@router.patch("/{organization_id}/cache-settings", response_model=CacheSettingsResponse)
def update_cache_settings_route(
    organization_id: str,
    request: CacheSettingsUpdateRequest,
    auth_context: AuthContext = Depends(require_auth_context),
) -> CacheSettingsResponse:
    require_org_admin(auth_context, organization_id)

    if request.cache_enabled is None and request.cache_ttl_seconds is None:
        raise HTTPException(
            status_code=422,
            detail="Provide cache_enabled and/or cache_ttl_seconds.",
        )

    store = get_cache_settings_store()
    if store is None:
        raise HTTPException(
            status_code=503, detail="Cache settings are not configured."
        )

    try:
        settings = store.upsert(
            organization_id,
            cache_enabled=request.cache_enabled,
            cache_ttl_seconds=request.cache_ttl_seconds,
        )
    except CacheSettingsStoreError:
        raise HTTPException(
            status_code=502, detail="Could not update cache settings."
        ) from None

    return _cache_settings_response(settings)

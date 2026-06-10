import anyio
import pytest
from fastapi import HTTPException

from app.auth import AuthContext, require_org_membership, validate_supabase_session


def test_member_access_returns_none() -> None:
    context = AuthContext(
        user_id="user_123",
        auth_required=True,
        memberships={"org_123"},
    )

    assert require_org_membership(context, "org_123") is None


def test_missing_org_membership_is_rejected() -> None:
    context = AuthContext(
        user_id="user_123",
        auth_required=True,
        memberships={"org_123"},
    )

    with pytest.raises(HTTPException) as exc_info:
        require_org_membership(context, "org_456")

    assert exc_info.value.status_code == 403


def test_auth_disabled_requires_explicit_demo_bypass() -> None:
    context = AuthContext(
        user_id=None,
        auth_required=False,
        memberships=set(),
    )

    with pytest.raises(HTTPException) as exc_info:
        require_org_membership(context, "org_demo")

    assert exc_info.value.status_code == 403


def test_auth_disabled_allows_local_demo_when_requested() -> None:
    context = AuthContext(
        user_id=None,
        auth_required=False,
        memberships=set(),
    )

    assert require_org_membership(context, "demo-org", allow_demo=True) is None


def test_auth_disabled_demo_bypass_rejects_non_demo_org() -> None:
    context = AuthContext(
        user_id=None,
        auth_required=False,
        memberships=set(),
    )

    with pytest.raises(HTTPException) as exc_info:
        require_org_membership(context, "org_456", allow_demo=True)

    assert exc_info.value.status_code == 403


def test_supabase_validation_seam_accepts_bearer_without_memberships() -> None:
    context = anyio.run(validate_supabase_session, "opaque-token")

    assert context.user_id == "authenticated"
    assert context.auth_required is True
    assert context.memberships == frozenset()

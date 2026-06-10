import logging

import anyio
import pytest
from fastapi import HTTPException

from app.auth import AuthContext, require_org_membership, validate_supabase_session
from app.config import Settings
from app.main import warn_when_auth_required_without_verifier


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


def test_supabase_validation_rejects_when_no_verifier_is_configured(
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.auth.supabase_session_verifier", None)

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(validate_supabase_session, "opaque-token")

    assert exc_info.value.status_code == 401


def test_supabase_validation_uses_verified_claims(monkeypatch) -> None:
    async def verifier(token: str) -> dict[str, object]:
        assert token == "opaque-token"
        return {"sub": "user_123"}

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)

    context = anyio.run(validate_supabase_session, " opaque-token ")

    assert context.user_id == "user_123"
    assert context.auth_required is True
    assert context.memberships == frozenset()


def test_auth_startup_warns_when_required_without_verifier(monkeypatch, caplog) -> None:
    monkeypatch.setattr("app.auth.supabase_session_verifier", None)
    caplog.set_level(logging.WARNING, logger="app.main")

    warn_when_auth_required_without_verifier(Settings(auth_required=True))

    assert "AUTH_REQUIRED=true" in caplog.text
    assert "Supabase session verifier" in caplog.text


def test_supabase_validation_rejects_non_mapping_claims(monkeypatch) -> None:
    async def verifier(token: str) -> list[str]:
        assert token == "opaque-token"
        return ["not", "claims"]

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(validate_supabase_session, "opaque-token")

    assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"sub": 123},
        {"sub": ""},
        {"sub": "   "},
    ],
)
def test_supabase_validation_rejects_missing_or_invalid_sub(
    monkeypatch,
    claims: dict[str, object],
) -> None:
    async def verifier(token: str) -> dict[str, object]:
        assert token == "opaque-token"
        return claims

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(validate_supabase_session, "opaque-token")

    assert exc_info.value.status_code == 401


def test_supabase_validation_derives_memberships_from_verified_claims(
    monkeypatch,
) -> None:
    async def verifier(token: str) -> dict[str, object]:
        assert token == "opaque-token"
        return {
            "sub": "user_123",
            "app_metadata": {
                "organization_ids": ["org_123"],
                "organizations": ["org_456"],
            },
            "memberships": ["org_789"],
        }

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)

    context = anyio.run(validate_supabase_session, "opaque-token")

    assert context.memberships == frozenset({"org_123", "org_456", "org_789"})

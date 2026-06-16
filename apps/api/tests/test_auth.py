import logging

import anyio
import httpx
import pytest
from fastapi import HTTPException

from app.auth import (
    AuthContext,
    SupabaseAuthServerVerifier,
    configure_supabase_session_verifier,
    require_org_membership,
    validate_supabase_session,
)
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


def test_startup_requires_service_role_key_when_auth_required() -> None:
    from app.main import require_membership_lookup_when_auth_required

    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        require_membership_lookup_when_auth_required(Settings(auth_required=True))


def test_startup_allows_service_role_key_present() -> None:
    from app.main import require_membership_lookup_when_auth_required

    require_membership_lookup_when_auth_required(
        Settings(auth_required=True, supabase_service_role_key="service-role-key")
    )


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


def test_validation_populates_memberships_from_live_lookup(monkeypatch) -> None:
    from app.services.membership_directory import Organization

    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    async def lookup(user_id: str) -> tuple[Organization, ...]:
        assert user_id == "user_123"
        return (Organization(id="org-1", name="Acme"),)

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)

    context = anyio.run(validate_supabase_session, "opaque-token", None, lookup)

    assert context.memberships == frozenset({"org-1"})
    assert tuple(context.organizations) == (Organization(id="org-1", name="Acme"),)


def test_validation_normalizes_membership_ids_from_lookup(monkeypatch) -> None:
    from app.services.membership_directory import Organization

    upper_uuid = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    canonical_uuid = upper_uuid.lower()

    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    async def lookup(user_id: str) -> tuple[Organization, ...]:
        assert user_id == "user_123"
        return (Organization(id=upper_uuid, name="Acme"),)

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)

    context = anyio.run(validate_supabase_session, "opaque-token", None, lookup)

    assert canonical_uuid in context.memberships
    assert require_org_membership(context, canonical_uuid) is None
    assert require_org_membership(context, upper_uuid) is None
    assert tuple(context.organizations) == (Organization(id=upper_uuid, name="Acme"),)


def test_validation_fails_closed_when_lookup_errors(monkeypatch) -> None:
    from app.services.membership_directory import MembershipLookupError

    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    async def lookup(user_id: str):
        raise MembershipLookupError()

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(validate_supabase_session, "opaque-token", None, lookup)

    assert exc_info.value.status_code == 401


def test_validation_without_lookup_yields_empty_memberships(monkeypatch) -> None:
    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)
    monkeypatch.setattr("app.auth.membership_lookup", None)

    context = anyio.run(validate_supabase_session, "opaque-token")

    assert context.memberships == frozenset()
    assert tuple(context.organizations) == ()


def test_supabase_auth_server_verifier_returns_claims() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "user_123",
                "app_metadata": {"organization_ids": ["org_123"]},
            },
        )

    verifier = SupabaseAuthServerVerifier(
        supabase_url="https://project.supabase.co",
        supabase_anon_key="anon-key",
        transport=httpx.MockTransport(handler),
    )

    claims = anyio.run(verifier, "opaque-token")

    assert claims == {
        "sub": "user_123",
        "app_metadata": {"organization_ids": ["org_123"]},
    }
    assert requests[0].url == "https://project.supabase.co/auth/v1/user"
    assert requests[0].headers["apikey"] == "anon-key"
    assert requests[0].headers["authorization"] == "Bearer opaque-token"


def test_supabase_auth_server_verifier_rejects_invalid_token() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid"})

    verifier = SupabaseAuthServerVerifier(
        supabase_url="https://project.supabase.co",
        supabase_anon_key="anon-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(validate_supabase_session, "opaque-token", verifier)

    assert exc_info.value.status_code == 401


def test_supabase_auth_server_verifier_rejects_transport_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private network detail")

    verifier = SupabaseAuthServerVerifier(
        supabase_url="https://project.supabase.co",
        supabase_anon_key="anon-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(validate_supabase_session, "opaque-token", verifier)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"


def test_configure_supabase_session_verifier_clears_missing_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    configure_supabase_session_verifier(Settings())
    from app import auth

    assert auth.supabase_session_verifier is not None

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    configure_supabase_session_verifier(Settings())

    assert auth.supabase_session_verifier is None


def test_supabase_auth_server_verifier_rejects_malformed_json() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    verifier = SupabaseAuthServerVerifier(
        supabase_url="https://project.supabase.co",
        supabase_anon_key="anon-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(validate_supabase_session, "opaque-token", verifier)

    assert exc_info.value.status_code == 401


def test_supabase_auth_server_verifier_rejects_non_mapping_payload() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "user"])

    verifier = SupabaseAuthServerVerifier(
        supabase_url="https://project.supabase.co",
        supabase_anon_key="anon-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HTTPException) as exc_info:
        anyio.run(validate_supabase_session, "opaque-token", verifier)

    assert exc_info.value.status_code == 401


def test_normalize_membership_id_lowercases_non_uuid_values() -> None:
    from app.auth import _normalize_membership_id

    assert _normalize_membership_id("ORG_ABC") == "org_abc"


def test_normalize_membership_id_canonicalizes_uuid_values() -> None:
    from app.auth import _normalize_membership_id

    assert (
        _normalize_membership_id("22222222-2222-4222-8222-ABCDEFABCDEF")
        == "22222222-2222-4222-8222-abcdefabcdef"
    )


def test_require_org_admin_allows_admin() -> None:
    from app.auth import require_org_admin
    from app.services.membership_directory import Organization

    context = AuthContext(
        user_id="u", auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="admin"),),
    )
    require_org_admin(context, "org-1")  # no raise


def test_require_org_admin_rejects_member() -> None:
    from app.auth import require_org_admin
    from app.services.membership_directory import Organization

    context = AuthContext(
        user_id="u", auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="member"),),
    )
    with pytest.raises(HTTPException) as exc:
        require_org_admin(context, "org-1")
    assert exc.value.status_code == 403

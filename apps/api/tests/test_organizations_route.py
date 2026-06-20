import pytest
from fastapi.testclient import TestClient

import app.services.connect_rate_limit as connect_rate_limit
from app.auth import AuthContext, require_auth_context
from app.main import app
from app.routes import organizations
from app.services.audit_events import audit_event_recorder
from app.services.connect_rate_limit import InMemoryConnectLimiter
from app.services.membership_directory import Organization
from app.services.org_invitations import AlreadyMemberError, InviteProvisioningError


@pytest.fixture(autouse=True)
def fresh_invite_limiter(monkeypatch):
    limiter = InMemoryConnectLimiter(max_attempts=100, window_seconds=300)
    monkeypatch.setattr(connect_rate_limit, "_invite_limiter", limiter)
    return limiter


@pytest.fixture(autouse=True)
def clear_audit_events():
    audit_event_recorder.clear()
    yield
    audit_event_recorder.clear()


def _admin_ctx() -> AuthContext:
    return AuthContext(
        user_id="actor-1",
        auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="owner"),),
    )


def _member_ctx() -> AuthContext:
    return AuthContext(
        user_id="actor-2",
        auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="member"),),
    )


def test_invite_succeeds_for_admin(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx
    seen = {}
    monkeypatch.setattr(
        organizations,
        "invite_member_to_org",
        lambda **kw: seen.update(kw) or kw["email"],
    )
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"email": "new@acme.com"}
    assert seen["actor_user_id"] == "actor-1"
    assert seen["organization_id"] == "org-1"


def test_invite_forbidden_for_member(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _member_ctx
    monkeypatch.setattr(
        organizations, "invite_member_to_org", lambda **kw: kw["email"]
    )
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 403


def test_invite_rejects_free_email(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx
    calls = []
    monkeypatch.setattr(
        organizations, "invite_member_to_org", lambda **kw: calls.append(kw)
    )
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "x@gmail.com"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 422
    assert calls == []


def test_invite_already_member_returns_409(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx

    def _raise(**kw):
        raise AlreadyMemberError("new@acme.com is already a member.")

    monkeypatch.setattr(organizations, "invite_member_to_org", _raise)
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 409
    assert "already a member" in response.json()["detail"]


def test_invite_upstream_failure_returns_502(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx

    def _raise(**kw):
        raise InviteProvisioningError("Could not send the invite.")

    monkeypatch.setattr(organizations, "invite_member_to_org", _raise)
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 502


def test_invite_rate_limited_returns_429(monkeypatch) -> None:
    monkeypatch.setattr(
        connect_rate_limit,
        "_invite_limiter",
        InMemoryConnectLimiter(max_attempts=1, window_seconds=300),
    )
    app.dependency_overrides[require_auth_context] = _admin_ctx
    monkeypatch.setattr(
        organizations, "invite_member_to_org", lambda **kw: kw["email"]
    )
    client = TestClient(app)
    statuses = [
        client.post(
            "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
        ).status_code
        for _ in range(2)
    ]
    app.dependency_overrides.clear()
    assert statuses[0] == 200
    assert statuses[1] == 429


def test_invite_records_audit_event(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx
    monkeypatch.setattr(
        organizations, "invite_member_to_org", lambda **kw: kw["email"]
    )
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    events = audit_event_recorder.list_events()
    assert len(events) == 1
    assert events[0]["event_name"] == "organization.member_invited"
    assert events[0]["organization_id"] == "org-1"
    assert events[0]["payload"]["email"] == "new@acme.com"
    assert events[0]["payload"]["actor_user_id"] == "actor-1"


def test_invite_unauthenticated_returns_401(monkeypatch) -> None:
    # No require_auth_context override: exercise the real guard with auth enabled
    # and no bearer token. The invite service must never be reached.
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    calls = []
    monkeypatch.setattr(
        organizations, "invite_member_to_org", lambda **kw: calls.append(kw)
    )
    client = TestClient(app)
    response = client.post(
        "/api/organizations/org-1/invitations", json={"email": "new@acme.com"}
    )
    assert response.status_code == 401
    assert calls == []
    assert audit_event_recorder.list_events() == []

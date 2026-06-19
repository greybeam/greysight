import pytest
from fastapi.testclient import TestClient

import app.services.connect_rate_limit as connect_rate_limit
from app.auth import AuthContext, require_auth_context
from app.main import app
from app.routes import onboarding
from app.services.connect_rate_limit import InMemoryConnectLimiter


@pytest.fixture(autouse=True)
def fresh_connect_limiter(monkeypatch):
    """Install a fresh, generous limiter before each test so the singleton
    never bleeds rate-limit / in-flight state across tests."""
    limiter = InMemoryConnectLimiter(max_attempts=100, window_seconds=300)
    monkeypatch.setattr(connect_rate_limit, "_limiter", limiter)
    return limiter


def _auth_context() -> AuthContext:
    return AuthContext(user_id="user-1", auth_required=True, memberships=frozenset())


def _payload() -> dict:
    return {
        "org_name": "Acme",
        "account": "GOPGUKF-JO19546",
        "user": "GREYBEAM_USER",
        "role": "GREYBEAM_ROLE",
        "warehouse": "GREYBEAM_WH",
        "private_key_pem": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----",
    }


def test_connect_validates_then_creates(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _auth_context
    monkeypatch.setattr(
        onboarding, "validate_snowflake_connection", lambda config: "XY12345"
    )
    created = {}
    monkeypatch.setattr(
        onboarding,
        "create_org_with_connection",
        lambda **kwargs: created.update(kwargs) or "org-123",
    )
    client = TestClient(app)
    response = client.post("/api/onboarding/connect", json=_payload())
    app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["id"] == "org-123"
    assert created["p_user_id"] == "user-1"  # identity from token, not body
    assert created["p_account"] == "GOPGUKF-JO19546"
    assert created["p_account_locator"] == "XY12345"


def test_connect_rejects_invalid_account(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _auth_context
    client = TestClient(app)
    bad = _payload() | {"account": "http://evil.example.com"}
    response = client.post("/api/onboarding/connect", json=bad)
    app.dependency_overrides.clear()
    assert response.status_code == 422


def test_connect_returns_422_and_persists_nothing_on_validation_failure(
    monkeypatch,
) -> None:
    from app.services.snowflake_client import SnowflakeValidationError

    app.dependency_overrides[require_auth_context] = _auth_context

    def _fail(config):
        raise SnowflakeValidationError(
            "Could not access required Snowflake Account Usage views."
        )

    monkeypatch.setattr(onboarding, "validate_snowflake_connection", _fail)
    calls = []
    monkeypatch.setattr(
        onboarding, "create_org_with_connection", lambda **k: calls.append(k)
    )
    client = TestClient(app)
    response = client.post("/api/onboarding/connect", json=_payload())
    app.dependency_overrides.clear()

    assert response.status_code == 422
    assert calls == []  # nothing persisted


def test_422_redacts_secret_input(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _auth_context
    client = TestClient(app)
    bad = _payload() | {"private_key_pem": {"leak": "PEMSECRETMARKER"}}
    response = client.post("/api/onboarding/connect", json=bad)
    app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "PEMSECRETMARKER" not in response.text


def test_passphrase_too_long_rejected(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _auth_context
    client = TestClient(app)
    bad = _payload() | {"passphrase": "PASSSECRETMARKER" + "x" * 2048}
    response = client.post("/api/onboarding/connect", json=bad)
    app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "PASSSECRETMARKER" not in response.text


def test_connect_rate_limited_returns_429(monkeypatch) -> None:
    # Override the autouse fixture with a small limit for this test.
    monkeypatch.setattr(
        connect_rate_limit,
        "_limiter",
        InMemoryConnectLimiter(max_attempts=2, window_seconds=300),
    )
    app.dependency_overrides[require_auth_context] = _auth_context
    monkeypatch.setattr(
        onboarding, "validate_snowflake_connection", lambda config: "XY12345"
    )
    monkeypatch.setattr(
        onboarding, "create_org_with_connection", lambda **kwargs: "org-123"
    )
    client = TestClient(app)
    statuses = [
        client.post("/api/onboarding/connect", json=_payload()).status_code
        for _ in range(3)
    ]
    app.dependency_overrides.clear()

    assert statuses[0] == 201
    assert statuses[1] == 201
    assert statuses[2] == 429


def test_connect_in_flight_returns_409(monkeypatch) -> None:
    app.dependency_overrides[require_auth_context] = _auth_context
    client = TestClient(app)
    reentrant = {}

    def _reenter(config):
        # Simulate a second concurrent attempt for the same user arriving while
        # the first is still inside the limiter guard.
        reentrant["status"] = client.post(
            "/api/onboarding/connect", json=_payload()
        ).status_code

    monkeypatch.setattr(onboarding, "validate_snowflake_connection", _reenter)
    monkeypatch.setattr(
        onboarding, "create_org_with_connection", lambda **kwargs: "org-123"
    )
    response = client.post("/api/onboarding/connect", json=_payload())
    app.dependency_overrides.clear()

    assert response.status_code == 201
    assert reentrant["status"] == 409


def test_connect_returns_422_on_malformed_key(monkeypatch) -> None:
    from app.services.snowflake_client import SnowflakeConfigurationError

    app.dependency_overrides[require_auth_context] = _auth_context

    def _bad_key(config):
        raise SnowflakeConfigurationError("Snowflake private key could not be loaded.")

    monkeypatch.setattr(onboarding, "validate_snowflake_connection", _bad_key)
    calls = []
    monkeypatch.setattr(
        onboarding, "create_org_with_connection", lambda **k: calls.append(k)
    )
    client = TestClient(app)
    response = client.post("/api/onboarding/connect", json=_payload())
    app.dependency_overrides.clear()

    assert response.status_code == 422
    assert calls == []  # nothing persisted


def test_disconnect_requires_admin(monkeypatch) -> None:
    from app.auth import AuthContext, require_auth_context
    from app.services.membership_directory import Organization

    member_ctx = AuthContext(
        user_id="u",
        auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="member"),),
    )
    app.dependency_overrides[require_auth_context] = lambda: member_ctx
    client = TestClient(app)
    response = client.post("/api/onboarding/org-1/disconnect")
    app.dependency_overrides.clear()
    assert response.status_code == 403


def test_disconnect_deletes_secret_for_admin(monkeypatch) -> None:
    from app.auth import AuthContext, require_auth_context
    from app.services.membership_directory import Organization

    admin_ctx = AuthContext(
        user_id="u",
        auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="owner"),),
    )
    app.dependency_overrides[require_auth_context] = lambda: admin_ctx
    disconnected = []
    monkeypatch.setattr(
        onboarding,
        "disconnect_org_connection",
        lambda org_id: disconnected.append(org_id),
    )
    client = TestClient(app)
    response = client.post("/api/onboarding/org-1/disconnect")
    app.dependency_overrides.clear()
    assert response.status_code == 204
    assert disconnected == ["org-1"]


def test_disconnect_is_idempotent(monkeypatch) -> None:
    from app.auth import AuthContext, require_auth_context
    from app.services.membership_directory import Organization

    admin_ctx = AuthContext(
        user_id="u",
        auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="owner"),),
    )
    app.dependency_overrides[require_auth_context] = lambda: admin_ctx
    monkeypatch.setattr(onboarding, "disconnect_org_connection", lambda org_id: None)
    client = TestClient(app)
    first = client.post("/api/onboarding/org-1/disconnect")
    second = client.post("/api/onboarding/org-1/disconnect")
    app.dependency_overrides.clear()
    assert first.status_code == 204
    assert second.status_code == 204

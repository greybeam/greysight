from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_events import audit_event_recorder
from app.services.snowflake_client import SnowflakeValidationError


def test_snowflake_validation_hidden_when_auth_required_unauthenticated(
    monkeypatch,
) -> None:
    """Under auth, the self-host route is invisible even to unauthenticated callers.

    The 404 must fire before any auth dependency resolves, so an unauthenticated
    caller gets 404 (route not available) rather than 401.
    """
    calls: list[bool] = []

    def validate() -> None:
        calls.append(True)

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setattr("app.routes.snowflake.validate_snowflake_connection", validate)

    response = TestClient(app).post("/api/snowflake/validate")

    assert response.status_code == 404
    assert response.json()["detail"] != "Authentication required"
    assert calls == []


def test_snowflake_validation_hidden_when_auth_required_authenticated(
    monkeypatch,
) -> None:
    """Under auth, even a fully authenticated caller gets 404, never env creds."""
    calls: list[bool] = []

    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    def validate() -> None:
        calls.append(True)

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)
    monkeypatch.setattr("app.routes.snowflake.validate_snowflake_connection", validate)

    response = TestClient(app).post(
        "/api/snowflake/validate", headers={"Authorization": "Bearer x"}
    )

    assert response.status_code == 404
    assert calls == []


def test_snowflake_validation_returns_success(monkeypatch) -> None:
    calls: list[bool] = []

    def validate() -> None:
        calls.append(True)

    monkeypatch.setattr("app.routes.snowflake.validate_snowflake_connection", validate)

    response = TestClient(app).post("/api/snowflake/validate")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Snowflake access validated."}
    assert calls == [True]


def test_snowflake_validation_returns_user_safe_error(monkeypatch) -> None:
    def fail_validation() -> None:
        raise SnowflakeValidationError("raw private backend detail")

    monkeypatch.setattr(
        "app.routes.snowflake.validate_snowflake_connection", fail_validation
    )

    response = TestClient(app).post("/api/snowflake/validate")

    assert response.status_code in {400, 403}
    assert "raw private backend detail" not in response.text
    assert (
        response.json()["detail"]
        == "Could not validate Snowflake Account Usage access."
    )


def test_snowflake_validation_internal_error_is_not_masked(monkeypatch) -> None:
    def fail_validation() -> None:
        raise RuntimeError("raw private backend detail")

    monkeypatch.setattr(
        "app.routes.snowflake.validate_snowflake_connection", fail_validation
    )

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/snowflake/validate"
    )

    assert response.status_code == 500
    assert "raw private backend detail" not in response.text


def test_snowflake_validation_audits_unexpected_validation_errors(monkeypatch) -> None:
    audit_event_recorder.clear()

    def fail_validation() -> None:
        raise RuntimeError("raw private backend detail")

    monkeypatch.setattr(
        "app.routes.snowflake.validate_snowflake_connection",
        fail_validation,
    )

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/snowflake/validate"
    )

    assert response.status_code == 500
    assert audit_event_recorder.list_events() == [
        {
            "event_name": "snowflake.validation_attempted",
            "organization_id": None,
            "payload": {"outcome": "error"},
        }
    ]
    assert "raw private backend detail" not in str(audit_event_recorder.list_events())


def test_snowflake_validation_error_returns_403_and_non_error_audit(
    monkeypatch,
) -> None:
    audit_event_recorder.clear()

    def fail_validation() -> None:
        raise SnowflakeValidationError("Could not validate Snowflake connection.")

    monkeypatch.setattr(
        "app.routes.snowflake.validate_snowflake_connection",
        fail_validation,
    )

    response = TestClient(app).post("/api/snowflake/validate")

    events = audit_event_recorder.list_events()
    assert response.status_code == 403
    assert len(events) == 1
    assert events[0] == {
        "event_name": "snowflake.validation_attempted",
        "organization_id": None,
        "payload": {"outcome": "failed"},
    }
    assert events[0]["payload"]["outcome"] != "error"

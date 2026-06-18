from fastapi.testclient import TestClient

from app.main import app
from app.services.membership_directory import Organization


def test_returns_caller_memberships(monkeypatch) -> None:
    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    async def lookup(user_id: str) -> tuple[Organization, ...]:
        assert user_id == "user_123"
        return (Organization(id="org-1", name="Acme", account_locator="IJ42635"),)

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)
    monkeypatch.setattr("app.auth.membership_lookup", lookup)

    response = TestClient(app).get(
        "/api/session/memberships", headers={"Authorization": "Bearer x"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "organizations": [
            {"id": "org-1", "name": "Acme", "account_locator": "IJ42635"}
        ]
    }


def test_requires_authentication(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_REQUIRED", "true")

    response = TestClient(app).get("/api/session/memberships")

    assert response.status_code in {401, 403}


def test_empty_when_no_memberships(monkeypatch) -> None:
    async def verifier(token: str) -> dict[str, object]:
        return {"sub": "user_123"}

    async def lookup(user_id: str) -> tuple[Organization, ...]:
        return ()

    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)
    monkeypatch.setattr("app.auth.membership_lookup", lookup)

    response = TestClient(app).get(
        "/api/session/memberships", headers={"Authorization": "Bearer x"}
    )

    assert response.status_code == 200
    assert response.json() == {"organizations": []}

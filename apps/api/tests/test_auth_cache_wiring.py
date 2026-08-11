from fastapi.testclient import TestClient

from app.services import auth_cache


def test_module_scope_wiring_configures_both_caches() -> None:
    """Importing app.main must leave the cache ready to use."""
    import app.main  # noqa: F401 — import side effect is the subject

    assert auth_cache._verify_cache is not None  # noqa: SLF001
    assert auth_cache._membership_cache is not None  # noqa: SLF001


def test_lifespan_reset_runs_before_clients_close(monkeypatch) -> None:
    import app.main

    order: list[str] = []

    async def fake_reset() -> None:
        order.append("auth-cache-reset")

    real_clear_clients = app.main.clear_clients

    def tracking_clear_clients() -> None:
        order.append("clients-cleared")
        real_clear_clients()

    monkeypatch.setattr(app.main.auth_cache, "reset", fake_reset)
    monkeypatch.setattr(app.main, "clear_clients", tracking_clear_clients)

    with TestClient(app.main.app):
        pass

    assert order == ["auth-cache-reset", "auth-cache-reset", "clients-cleared"]


def test_two_authenticated_requests_hit_the_verifier_once_through_the_app(
    monkeypatch,
) -> None:
    """Route-level coverage of the real wiring — no seams, a real request."""
    import app.main
    from app.config import Settings
    from app.services.membership_directory import Organization

    auth_cache.configure_auth_cache(Settings(GREYSIGHT_AUTH_CACHE_TTL_SECONDS=30))

    verifier_calls: list[int] = []
    lookup_calls: list[int] = []

    async def verifier(token: str) -> dict[str, object]:
        verifier_calls.append(1)
        return {"sub": "user-1"}

    async def lookup(user_id: str) -> tuple[Organization, ...]:
        lookup_calls.append(1)
        return (Organization(id="org-1", name="Acme", role="owner"),)

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)
    monkeypatch.setattr("app.auth.membership_lookup", lookup)
    # require_auth_context constructs Settings() per request, so the env var
    # is enough to turn auth on for these two requests.
    monkeypatch.setenv("AUTH_REQUIRED", "true")

    with TestClient(app.main.app) as client:
        headers = {"Authorization": "Bearer opaque-token"}
        first = client.get("/api/session/memberships", headers=headers)
        second = client.get("/api/session/memberships", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {
        "organizations": [
            {
                "id": "org-1",
                "name": "Acme",
                "role": "owner",
                "account_locator": None,
            }
        ]
    }
    assert second.json() == first.json()
    assert len(verifier_calls) == 1
    assert len(lookup_calls) == 1

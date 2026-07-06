import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth import AuthContext, require_auth_context
from app.main import app
from app.services import dashboard_cache_settings
from app.services.dashboard_cache_settings import (
    DEFAULT_CACHE_TTL_SECONDS,
    InMemoryCacheSettingsStore,
    SupabaseCacheSettingsStore,
    configure_cache_settings_store,
)
from app.services.membership_directory import Organization


@pytest.fixture(autouse=True)
def _fresh_store():
    store = InMemoryCacheSettingsStore()
    configure_cache_settings_store(store)
    yield store
    configure_cache_settings_store(None)


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


def test_get_returns_defaults_when_no_row() -> None:
    app.dependency_overrides[require_auth_context] = _member_ctx
    try:
        response = TestClient(app).get("/api/organizations/org-1/cache-settings")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {
        "cache_enabled": True,
        "cache_ttl_seconds": DEFAULT_CACHE_TTL_SECONDS,
    }


def test_get_requires_membership() -> None:
    def _other_org() -> AuthContext:
        return AuthContext(
            user_id="x",
            auth_required=True,
            memberships=frozenset({"other"}),
            organizations=(Organization(id="other", name="Other", role="owner"),),
        )

    app.dependency_overrides[require_auth_context] = _other_org
    try:
        response = TestClient(app).get("/api/organizations/org-1/cache-settings")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


def test_patch_by_admin_upserts_and_returns_full_settings(_fresh_store) -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx
    try:
        response = TestClient(app).patch(
            "/api/organizations/org-1/cache-settings",
            json={"cache_enabled": False, "cache_ttl_seconds": 7200},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"cache_enabled": False, "cache_ttl_seconds": 7200}
    assert _fresh_store.get("org-1") == dashboard_cache_settings.CacheSettings(
        cache_enabled=False, cache_ttl_seconds=7200
    )


def test_patch_partial_preserves_other_field(_fresh_store) -> None:
    _fresh_store.upsert("org-1", cache_enabled=False, cache_ttl_seconds=7200)
    app.dependency_overrides[require_auth_context] = _admin_ctx
    try:
        response = TestClient(app).patch(
            "/api/organizations/org-1/cache-settings",
            json={"cache_enabled": True},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"cache_enabled": True, "cache_ttl_seconds": 7200}


def test_patch_by_member_returns_403(_fresh_store) -> None:
    app.dependency_overrides[require_auth_context] = _member_ctx
    try:
        response = TestClient(app).patch(
            "/api/organizations/org-1/cache-settings",
            json={"cache_enabled": False},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
    # A rejected mutation must not have written anything.
    assert _fresh_store.get("org-1") is None


@pytest.mark.parametrize("ttl_seconds", [60, 999_999])
def test_patch_rejects_ttl_outside_allowed_range(ttl_seconds: int) -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx
    try:
        response = TestClient(app).patch(
            "/api/organizations/org-1/cache-settings",
            json={"cache_ttl_seconds": ttl_seconds},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_patch_rejects_empty_body() -> None:
    app.dependency_overrides[require_auth_context] = _admin_ctx
    try:
        response = TestClient(app).patch(
            "/api/organizations/org-1/cache-settings", json={}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_supabase_partial_upsert_sends_only_provided_columns() -> None:
    # Pins the PostgREST contract: merge-duplicates updates exactly the columns
    # present in the payload, so a TTL-only PATCH must send organization_id and
    # cache_ttl_seconds and nothing else (no cache_enabled key), in one request.
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201, json=[{"cache_enabled": True, "cache_ttl_seconds": 7200}]
        )

    store = SupabaseCacheSettingsStore(
        supabase_url="https://example.supabase.co",
        service_role_key="svc",
        transport=httpx.MockTransport(handler),
    )

    store.upsert("org-1", cache_ttl_seconds=7200)

    assert len(requests) == 1
    request = requests[0]
    assert dict(request.url.params) == {"on_conflict": "organization_id"}
    assert json.loads(request.content) == {
        "organization_id": "org-1",
        "cache_ttl_seconds": 7200,
    }

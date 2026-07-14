from datetime import datetime
import logging

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError
import pytest

from app.auth import AuthContext, require_auth_context
from app.main import app
from app.routes import automated_savings
from app.services.automated_savings_store import (
    AutomatedSavingsStoreError,
    SupabaseAutomatedSavingsStore,
)
from app.services.membership_directory import Organization
from app.services.org_connection_resolver import OrgConnectionNotConfiguredError


def _admin_ctx():
    return AuthContext(
        user_id="u",
        auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="owner"),),
    )


def _member_ctx():
    return AuthContext(
        user_id="u",
        auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="member"),),
    )


def _cross_org_ctx():
    return AuthContext(
        user_id="u",
        auth_required=True,
        memberships=frozenset({"org-2"}),
        organizations=(Organization(id="org-2", name="Other", role="owner"),),
    )


def _unauthenticated():
    raise HTTPException(status_code=401, detail="Authentication required")


def _forbidden_seam(*args, **kwargs):
    pytest.fail("authorization must reject before protected dependencies are called")


def _secret_admin_ctx():
    return AuthContext(
        user_id="u",
        auth_required=True,
        memberships=frozenset({"secret-org-sentinel"}),
        organizations=(
            Organization(id="secret-org-sentinel", name="Hidden", role="owner"),
        ),
    )


class _CaptureStore:
    def __init__(self, captured):
        self.captured = captured

    def upsert_enrollment(self, organization_id, warehouse_name, **fields):
        self.captured.update(
            organization_id=organization_id,
            warehouse_name=warehouse_name,
            **fields,
        )


@pytest.mark.parametrize(
    ("auth_provider", "method", "path", "payload", "expected_status"),
    [
        (_unauthenticated, "GET", "/status", None, 401),
        (_unauthenticated, "GET", "/warehouses", None, 401),
        (_unauthenticated, "POST", "/check-access", None, 401),
        (_unauthenticated, "POST", "/agree", None, 401),
        (_unauthenticated, "POST", "/global-switch", {"enabled": True}, 401),
        (
            _unauthenticated,
            "POST",
            "/warehouses/WH1/toggle",
            {"enabled": True},
            401,
        ),
        (_cross_org_ctx, "GET", "/status", None, 403),
        (_cross_org_ctx, "GET", "/warehouses", None, 403),
        (_cross_org_ctx, "POST", "/check-access", None, 403),
        (_member_ctx, "POST", "/agree", None, 403),
        (_member_ctx, "POST", "/global-switch", {"enabled": True}, 403),
        (
            _member_ctx,
            "POST",
            "/warehouses/WH1/toggle",
            {"enabled": True},
            403,
        ),
    ],
)
def test_authorization_matrix_rejects_before_protected_calls(
    monkeypatch, auth_provider, method, path, payload, expected_status
):
    app.dependency_overrides[require_auth_context] = auth_provider
    monkeypatch.setattr(automated_savings, "_require_store", _forbidden_seam)
    monkeypatch.setattr(
        automated_savings, "get_automated_savings_store", _forbidden_seam
    )
    monkeypatch.setattr(automated_savings, "_resolve_role_name", _forbidden_seam)
    monkeypatch.setattr(
        "app.services.org_connection_resolver.resolve_snowflake_config",
        _forbidden_seam,
    )
    monkeypatch.setattr(
        "app.services.snowflake_runtime.get_connection_fetcher", _forbidden_seam
    )

    response = TestClient(app).request(
        method,
        f"/api/automated-savings/org-1{path}",
        json=payload,
    )
    app.dependency_overrides.clear()

    assert response.status_code == expected_status


@pytest.mark.parametrize(
    "created_on", ["2026-01-01T00:00:00Z", "2026-01-01T05:30:00+05:30"]
)
def test_enrollment_persists_aware_identity_without_defaults(monkeypatch, created_on):
    app.dependency_overrides[require_auth_context] = _admin_ctx
    monkeypatch.setattr(
        automated_savings,
        "capture_warehouse_identity",
        lambda **kwargs: automated_savings.CapturedWarehouseIdentity(
            warehouse_created_on=created_on
        ),
    )
    captured = {}
    monkeypatch.setattr(
        automated_savings, "_require_store", lambda: _CaptureStore(captured)
    )
    response = TestClient(app).post(
        "/api/automated-savings/org-1/warehouses/WH1/toggle",
        json={"enabled": True},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert set(captured) == {
        "organization_id",
        "warehouse_name",
        "enabled",
        "warehouse_created_on",
    }
    assert captured["warehouse_created_on"] == created_on


def test_disable_does_not_require_live_warehouse_identity(monkeypatch):
    app.dependency_overrides[require_auth_context] = _admin_ctx
    captured = {}

    class _DisableStore:
        def unenroll(self, organization_id, warehouse_name):
            captured.update(
                organization_id=organization_id, warehouse_name=warehouse_name
            )

    monkeypatch.setattr(
        automated_savings,
        "capture_warehouse_identity",
        lambda **kwargs: pytest.fail("disable must not query Snowflake identity"),
    )
    monkeypatch.setattr(automated_savings, "_require_store", lambda: _DisableStore())

    response = TestClient(app).post(
        "/api/automated-savings/org-1/warehouses/CaseSensitiveWH/toggle",
        json={"enabled": False},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured == {
        "organization_id": "org-1",
        "warehouse_name": "CaseSensitiveWH",
    }


def test_capture_warehouse_identity_requires_exact_live_name(monkeypatch):
    monkeypatch.setattr(
        "app.services.org_connection_resolver.resolve_snowflake_config",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "app.services.snowflake_runtime.get_connection_fetcher",
        lambda settings: object(),
    )
    monkeypatch.setattr(
        "app.services.warehouse_directory.list_live_warehouses",
        lambda config: [
            {
                "name": "CaseSensitiveWH",
                "created_on": "2026-01-01T00:00:00Z",
            }
        ],
    )

    captured = automated_savings.capture_warehouse_identity(
        organization_id="org-1",
        warehouse_name="casesensitivewh",
        settings=automated_savings.Settings(),
    )
    exact = automated_savings.capture_warehouse_identity(
        organization_id="org-1",
        warehouse_name="CaseSensitiveWH",
        settings=automated_savings.Settings(),
    )

    assert captured.warehouse_created_on is None
    assert exact.warehouse_created_on == "2026-01-01T00:00:00Z"


def test_management_routes_are_absent():
    paths = {route.path for route in app.routes}
    assert not any(path.endswith("/managed-default") for path in paths)
    assert not any(path.endswith("/reconcile") for path in paths)


@pytest.mark.parametrize(
    "created_on",
    [
        None,
        "",
        "not-a-timestamp",
        "2026-01-01",
        "2026-01-01T00:00:00",
        datetime(2026, 1, 1),
    ],
)
def test_enrollment_rejects_missing_or_malformed_identity(monkeypatch, created_on):
    app.dependency_overrides[require_auth_context] = _admin_ctx
    monkeypatch.setattr(
        automated_savings,
        "capture_warehouse_identity",
        lambda **kwargs: automated_savings.CapturedWarehouseIdentity(
            warehouse_created_on=created_on
        ),
    )
    monkeypatch.setattr(
        automated_savings,
        "_require_store",
        lambda: pytest.fail("invalid identity must not be persisted"),
    )
    response = TestClient(app).post(
        "/api/automated-savings/org-1/warehouses/WH1/toggle",
        json={"enabled": True},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 422


def test_warehouse_response_rejects_status_outside_public_contract():
    payload = {
        "name": "WH1",
        "size": "X-Small",
        "state": "STARTED",
        "type": "STANDARD",
        "supported": True,
        "min_cluster_count": 1,
        "max_cluster_count": 1,
        "started_clusters": 1,
        "auto_resume_ok": True,
        "auto_suspend": 300,
        "quiescing": 0,
        "enabled": True,
        "status": "drifted",
    }

    with pytest.raises(ValidationError):
        automated_savings.WarehouseResponse.model_validate(payload)


def test_list_warehouses_hides_store_failure(monkeypatch, caplog):
    app.dependency_overrides[require_auth_context] = _member_ctx
    secret_body = "secret-postgrest-body"
    store = SupabaseAutomatedSavingsStore(
        supabase_url="https://secret-project.supabase.co",
        service_role_key="secret-service-role-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, text=secret_body)
        ),
    )

    monkeypatch.setattr(
        "app.services.org_connection_resolver.resolve_snowflake_config",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "app.services.snowflake_runtime.get_connection_fetcher",
        lambda settings: object(),
    )
    monkeypatch.setattr(
        "app.services.warehouse_directory.list_live_warehouses",
        lambda config: [],
    )
    monkeypatch.setattr(automated_savings, "get_automated_savings_store", lambda: store)

    with caplog.at_level(logging.WARNING):
        response = TestClient(app).get("/api/automated-savings/org-1/warehouses")
    app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json() == {"detail": "Could not load warehouse enrollments."}
    assert secret_body not in response.text
    assert "operation=list_warehouse_enrollments" in caplog.text
    assert "kind=http_status" in caplog.text
    assert "status=503" in caplog.text
    assert secret_body not in caplog.text
    assert "secret-service-role-key" not in caplog.text
    assert "secret-project.supabase.co" not in caplog.text


def test_enrollment_store_failure_logs_only_sanitized_metadata(monkeypatch, caplog):
    app.dependency_overrides[require_auth_context] = _secret_admin_ctx

    class _FailingStore:
        def upsert_enrollment(self, *args, **kwargs):
            raise AutomatedSavingsStoreError(kind="http_status", status_code=409)

    monkeypatch.setattr(
        automated_savings,
        "capture_warehouse_identity",
        lambda **kwargs: automated_savings.CapturedWarehouseIdentity(
            warehouse_created_on="2026-01-01T00:00:00Z"
        ),
    )
    monkeypatch.setattr(automated_savings, "_require_store", lambda: _FailingStore())

    with caplog.at_level(logging.WARNING):
        response = TestClient(app).post(
            "/api/automated-savings/secret-org-sentinel/"
            "warehouses/SECRET_WAREHOUSE_SENTINEL/toggle",
            json={"enabled": True},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json() == {"detail": "Could not enroll the warehouse."}
    assert "operation=upsert_warehouse_enrollment" in caplog.text
    assert "kind=http_status" in caplog.text
    assert "status=409" in caplog.text
    assert "secret-org-sentinel" not in caplog.text
    assert "SECRET_WAREHOUSE_SENTINEL" not in caplog.text
    assert "secret-org-sentinel" not in response.text
    assert "SECRET_WAREHOUSE_SENTINEL" not in response.text


def test_status_role_name_null_when_snowflake_not_configured(monkeypatch):
    app.dependency_overrides[require_auth_context] = _member_ctx

    def _raise(organization_id, settings, *, fetch_connection):
        raise OrgConnectionNotConfiguredError("no config")

    monkeypatch.setattr(
        "app.services.org_connection_resolver.resolve_snowflake_config", _raise
    )
    response = TestClient(app).get("/api/automated-savings/org-1/status")
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["role_name"] is None

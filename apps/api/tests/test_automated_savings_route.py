from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.auth import AuthContext, require_auth_context
from app.main import app
from app.services.membership_directory import Organization
from app.services.org_connection_resolver import OrgConnectionNotConfiguredError
from app.routes import automated_savings


def _admin_ctx():
    return AuthContext(user_id="u", auth_required=True, memberships=frozenset({"org-1"}),
                       organizations=(Organization(id="org-1", name="Acme", role="owner"),))


def _member_ctx():
    return AuthContext(user_id="u", auth_required=True, memberships=frozenset({"org-1"}),
                       organizations=(Organization(id="org-1", name="Acme", role="member"),))


def test_member_cannot_flip_global_switch():
    app.dependency_overrides[require_auth_context] = _member_ctx
    client = TestClient(app)
    resp = client.post("/api/automated-savings/org-1/global-switch", json={"enabled": True})
    app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_managed_default_below_floor_rejected(monkeypatch):
    app.dependency_overrides[require_auth_context] = _admin_ctx
    client = TestClient(app)
    resp = client.post("/api/automated-savings/org-1/warehouses/WH1/managed-default",
                       json={"value": 45})
    app.dependency_overrides.clear()
    assert resp.status_code == 422


def test_enroll_rejects_sentinel_default(monkeypatch):
    app.dependency_overrides[require_auth_context] = _admin_ctx
    # Live warehouse captured at AUTO_SUSPEND=1 → cannot enroll.
    monkeypatch.setattr(
        automated_savings,
        "capture_stored_default",
        lambda **kw: automated_savings.CapturedWarehouseDefaults(
            stored_default=1, warehouse_created_on="2026-01-01T00:00:00Z"
        ),
    )
    client = TestClient(app)
    resp = client.post("/api/automated-savings/org-1/warehouses/WH1/toggle", json={"enabled": True})
    app.dependency_overrides.clear()
    assert resp.status_code == 422


def test_enroll_persists_live_warehouse_created_on(monkeypatch):
    app.dependency_overrides[require_auth_context] = _admin_ctx
    monkeypatch.setattr(
        automated_savings,
        "capture_stored_default",
        lambda **kw: automated_savings.CapturedWarehouseDefaults(
            stored_default=120, warehouse_created_on="2026-01-01T00:00:00Z"
        ),
    )

    captured_kwargs: dict = {}

    class _FakeStore:
        def upsert_enrollment(self, organization_id, warehouse_name, **kwargs):
            captured_kwargs["organization_id"] = organization_id
            captured_kwargs["warehouse_name"] = warehouse_name
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(automated_savings, "_require_store", lambda: _FakeStore())

    client = TestClient(app)
    resp = client.post("/api/automated-savings/org-1/warehouses/WH1/toggle", json={"enabled": True})
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert captured_kwargs["warehouse_created_on"] == "2026-01-01T00:00:00Z"
    assert captured_kwargs["warehouse_created_on"] is not None


def test_enroll_floors_managed_default_below_60(monkeypatch):
    # A warehouse whose current AUTO_SUSPEND is between the sentinel (0/1) and
    # the floor (60) is still eligible — its real value is kept as the immutable
    # stored_default, but managed_default (the restore target the DB constrains
    # to >= 60) must be floored, or the enroll write hits the CHECK and 502s.
    app.dependency_overrides[require_auth_context] = _admin_ctx
    monkeypatch.setattr(
        automated_savings,
        "capture_stored_default",
        lambda **kw: automated_savings.CapturedWarehouseDefaults(
            stored_default=30, warehouse_created_on="2026-01-01T00:00:00Z"
        ),
    )

    captured_kwargs: dict = {}

    class _FakeStore:
        def upsert_enrollment(self, organization_id, warehouse_name, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(automated_savings, "_require_store", lambda: _FakeStore())

    client = TestClient(app)
    resp = client.post("/api/automated-savings/org-1/warehouses/WH1/toggle", json={"enabled": True})
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert captured_kwargs["stored_default"] == 30
    assert captured_kwargs["managed_default"] == 60


def test_status_includes_role_name(monkeypatch):
    app.dependency_overrides[require_auth_context] = _member_ctx
    monkeypatch.setattr(
        "app.services.org_connection_resolver.resolve_snowflake_config",
        lambda organization_id, settings, *, fetch_connection: SimpleNamespace(
            role="ANALYST_ROLE"
        ),
    )
    client = TestClient(app)
    resp = client.get("/api/automated-savings/org-1/status")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["role_name"] == "ANALYST_ROLE"


def test_status_role_name_null_when_snowflake_not_configured(monkeypatch):
    app.dependency_overrides[require_auth_context] = _member_ctx

    def _raise(organization_id, settings, *, fetch_connection):
        raise OrgConnectionNotConfiguredError("no config")

    monkeypatch.setattr(
        "app.services.org_connection_resolver.resolve_snowflake_config", _raise
    )
    client = TestClient(app)
    resp = client.get("/api/automated-savings/org-1/status")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["role_name"] is None


def test_check_access_includes_role_name(monkeypatch):
    app.dependency_overrides[require_auth_context] = _member_ctx
    monkeypatch.setattr(
        "app.services.org_connection_resolver.resolve_snowflake_config",
        lambda organization_id, settings, *, fetch_connection: SimpleNamespace(
            role="ANALYST_ROLE"
        ),
    )
    monkeypatch.setattr(
        "app.services.warehouse_directory.check_manage_warehouses_grant",
        lambda config, role_name: True,
    )
    client = TestClient(app)
    resp = client.post("/api/automated-savings/org-1/check-access")
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["role_name"] == "ANALYST_ROLE"
    assert body["grant_present"] is True

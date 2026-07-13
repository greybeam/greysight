from fastapi.testclient import TestClient

from app.auth import AuthContext, require_auth_context
from app.main import app
from app.services.membership_directory import Organization
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
    monkeypatch.setattr(automated_savings, "capture_stored_default", lambda **kw: 1)
    client = TestClient(app)
    resp = client.post("/api/automated-savings/org-1/warehouses/WH1/toggle", json={"enabled": True})
    app.dependency_overrides.clear()
    assert resp.status_code == 422

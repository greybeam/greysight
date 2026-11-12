from datetime import date

from fastapi.testclient import TestClient
import pytest

from app.auth import AuthContext, require_auth_context
from app.main import app
from app.routes import automated_savings
from app.services.automated_savings_store import (
    AutomatedSavingsStoreError,
    DailySavingsRow,
)
from app.services.membership_directory import Organization
from app.services.ttl_cache import TtlCache


def _member_ctx():
    return AuthContext(
        user_id="u",
        auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="member"),),
    )


class _SavingsStore:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.calls = []

    def daily_savings(self, organization_id, day_count, end_day, now):
        self.calls.append((organization_id, day_count, end_day))
        if self.error is not None:
            raise self.error
        return self.rows


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(
        automated_savings, "_stats_cache", TtlCache(ttl_seconds=60.0, max_entries=256)
    )


@pytest.fixture()
def _member(monkeypatch):
    app.dependency_overrides[require_auth_context] = _member_ctx
    monkeypatch.setattr(automated_savings, "_utc_today", lambda: date(2026, 7, 15))
    yield
    app.dependency_overrides.clear()


def _row(day, wh, *, credits, seconds, count=1, assumed=0):
    return DailySavingsRow(
        day=day,
        warehouse_name=wh,
        suspension_count=count,
        avoided_seconds=seconds,
        avoided_credits=credits,
        assumed_settings_count=assumed,
    )


def test_savings_converts_credits_to_dollars_and_zero_fills(monkeypatch, _member):
    store = _SavingsStore(
        [
            _row("2026-07-09", "COMPUTE_WH", credits=2.0, seconds=3600, count=4),
            _row("2026-07-12", "ANALYTICS_WH", credits=0.5, seconds=1800, assumed=1),
        ]
    )
    monkeypatch.setattr(automated_savings, "_require_store", lambda: store)

    response = TestClient(app).get("/api/automated-savings/org-1/stats/savings?days=7")

    assert response.status_code == 200
    body = response.json()
    assert store.calls == [("org-1", 7, "2026-07-15")]
    assert body["price_per_credit"] == 3.0
    assert body["total_dollars"] == 7.5
    assert body["total_avoided_hours"] == 1.5
    assert body["total_suspensions"] == 5
    assert body["assumed_settings_count"] == 1
    assert body["warehouses"] == ["ANALYTICS_WH", "COMPUTE_WH"]
    assert [b["day"] for b in body["buckets"]][0] == "2026-07-09"
    assert len(body["buckets"]) == 7
    assert body["buckets"][0]["dollars"] == {"ANALYTICS_WH": 0.0, "COMPUTE_WH": 6.0}
    assert body["buckets"][3]["dollars"] == {"ANALYTICS_WH": 1.5, "COMPUTE_WH": 0.0}


def test_savings_rejects_unsupported_window(monkeypatch, _member):
    monkeypatch.setattr(automated_savings, "_require_store", lambda: _SavingsStore())

    response = TestClient(app).get("/api/automated-savings/org-1/stats/savings?days=14")

    assert response.status_code == 422


def test_savings_is_cached_per_org_and_day(monkeypatch, _member):
    store = _SavingsStore([])
    monkeypatch.setattr(automated_savings, "_require_store", lambda: store)
    client = TestClient(app)

    client.get("/api/automated-savings/org-1/stats/savings?days=7")
    client.get("/api/automated-savings/org-1/stats/savings?days=7")

    assert len(store.calls) == 1


def test_savings_store_failure_is_502(monkeypatch, _member):
    store = _SavingsStore(error=AutomatedSavingsStoreError(kind="transport"))
    monkeypatch.setattr(automated_savings, "_require_store", lambda: store)

    response = TestClient(app).get("/api/automated-savings/org-1/stats/savings?days=7")

    assert response.status_code == 502


def test_savings_requires_membership(monkeypatch):
    app.dependency_overrides[require_auth_context] = lambda: AuthContext(
        user_id="u",
        auth_required=True,
        memberships=frozenset({"org-2"}),
        organizations=(Organization(id="org-2", name="Other", role="owner"),),
    )
    try:
        response = TestClient(app).get(
            "/api/automated-savings/org-1/stats/savings?days=7"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403

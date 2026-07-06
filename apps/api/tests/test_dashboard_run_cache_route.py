from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthContext, require_auth_context
from app.main import app
from app.routes.dashboard_runs import (
    _reset_cached_snapshot_registry,
    _source_bounds_for_dataset_rows,
    dashboard_run_repository,
)
from app.services.dashboard_cache_settings import (
    InMemoryCacheSettingsStore,
    configure_cache_settings_store,
)
from app.services.dashboard_run_cache import (
    CachedDashboardRun,
    InMemoryRunCacheStore,
    configure_run_cache_store,
)
from app.services.demo_data import DEMO_ACCOUNT_LOCATOR, build_demo_dashboard_dataset
from app.services.membership_directory import Organization

ORG_ID = "00000000-0000-0000-0000-000000000001"


def _parse_iso(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(text)


@pytest.fixture(autouse=True)
def _stores():
    dashboard_run_repository.clear()
    _reset_cached_snapshot_registry()
    settings_store = InMemoryCacheSettingsStore()
    run_store = InMemoryRunCacheStore()
    configure_cache_settings_store(settings_store)
    configure_run_cache_store(run_store)
    yield settings_store, run_store
    configure_cache_settings_store(None)
    configure_run_cache_store(None)
    dashboard_run_repository.clear()
    _reset_cached_snapshot_registry()


def _member_ctx() -> AuthContext:
    return AuthContext(
        user_id="actor-1",
        auth_required=True,
        memberships=frozenset({ORG_ID}),
        organizations=(
            Organization(
                id=ORG_ID,
                name="Acme",
                role="member",
                account_locator=DEMO_ACCOUNT_LOCATOR,
            ),
        ),
    )


def _cached_run_from_demo(
    *, completed_at: datetime, expires_at: datetime
) -> CachedDashboardRun:
    payload = build_demo_dashboard_dataset()
    bounds = _source_bounds_for_dataset_rows(payload.datasets)
    return CachedDashboardRun(
        organization_id=ORG_ID,
        run_id="00000000-0000-0000-0000-0000000000aa",
        source="snowflake",
        window_days=payload.run.window_days,
        account_locator=DEMO_ACCOUNT_LOCATOR,
        summary=payload.summary.model_dump(mode="json"),
        metadata=payload.metadata.model_dump(mode="json"),
        datasets=payload.datasets,
        source_start_date=bounds.source_start_date,
        source_end_date=bounds.source_end_date,
        completed_at=completed_at,
        expires_at=expires_at,
    )


def _seed_active_cached_run(run_store: InMemoryRunCacheStore) -> datetime:
    now = datetime.now(timezone.utc)
    completed_at = now - timedelta(hours=1)
    run_store.upsert(
        _cached_run_from_demo(
            completed_at=completed_at, expires_at=now + timedelta(hours=23)
        )
    )
    return completed_at


def test_cached_returns_204_when_no_cached_run(_stores) -> None:
    app.dependency_overrides[require_auth_context] = _member_ctx
    try:
        response = TestClient(app).get(
            f"/api/dashboard-runs/cached?organization_id={ORG_ID}"
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 204
    assert response.content == b""


def test_cached_returns_204_when_cache_disabled(_stores) -> None:
    settings_store, run_store = _stores
    settings_store.upsert(ORG_ID, cache_enabled=False)
    _seed_active_cached_run(run_store)

    app.dependency_overrides[require_auth_context] = _member_ctx
    try:
        response = TestClient(app).get(
            f"/api/dashboard-runs/cached?organization_id={ORG_ID}"
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 204


def test_cached_returns_204_when_expired(_stores) -> None:
    _settings_store, run_store = _stores
    now = datetime.now(timezone.utc)
    run_store.upsert(
        _cached_run_from_demo(
            completed_at=now - timedelta(hours=48),
            expires_at=now - timedelta(hours=1),
        )
    )
    app.dependency_overrides[require_auth_context] = _member_ctx
    try:
        response = TestClient(app).get(
            f"/api/dashboard-runs/cached?organization_id={ORG_ID}"
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 204


def test_cached_hit_returns_run_and_renders_nondefault_window(_stores) -> None:
    _settings_store, run_store = _stores
    completed_at = _seed_active_cached_run(run_store)

    app.dependency_overrides[require_auth_context] = _member_ctx
    try:
        client = TestClient(app)
        response = client.get(f"/api/dashboard-runs/cached?organization_id={ORG_ID}")
        assert response.status_code == 200
        body = response.json()
        assert _parse_iso(body["cached_as_of"]) == completed_at
        run = body["run"]
        assert run["status"] == "completed"
        run_id = run["id"]

        # A non-default window (7d) must render off the cached datasets with no
        # re-query — the run was loaded into the in-memory repo.
        view_response = client.get(f"/api/dashboard-runs/{run_id}/view?window_days=7")
        assert view_response.status_code == 200
        view = view_response.json()
        assert view["range"]["window_days"] == 7
    finally:
        app.dependency_overrides.clear()


def test_cached_requires_membership(_stores) -> None:
    _settings_store, run_store = _stores
    _seed_active_cached_run(run_store)

    def _other_org() -> AuthContext:
        return AuthContext(
            user_id="x",
            auth_required=True,
            memberships=frozenset({"other"}),
            organizations=(Organization(id="other", name="Other", role="member"),),
        )

    app.dependency_overrides[require_auth_context] = _other_org
    try:
        response = TestClient(app).get(
            f"/api/dashboard-runs/cached?organization_id={ORG_ID}"
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


def test_snowflake_finalize_writes_cache_when_enabled(_stores, monkeypatch) -> None:
    from tests.test_snowflake_dashboard_run import (
        _source_key_for_sql,
        _source_rows,
        _wait_terminal,
    )
    from app.services.dashboard_datasets import FETCH_WINDOW_DAYS

    _settings_store, run_store = _stores
    monkeypatch.setenv("DATA_SOURCE", "snowflake")

    def execute(
        sql: str, bind_params: dict[str, Any], config: Any = None
    ) -> list[dict[str, Any]]:
        lowered = sql.lower()
        if "current_account()" in lowered:
            return [{"account_locator": "TU24199"}]
        if "remaining_balance_daily" in lowered:
            return [
                {
                    "usage_date": "2026-06-05",
                    "currency": "USD",
                    "balance": 15_000.0,
                }
            ]
        if "organization_usage" in lowered:
            if "usage_in_currency_daily" in lowered:
                return [
                    {
                        "usage_date": "2026-06-05",
                        "service_type": "WAREHOUSE_METERING",
                        "rating_type": "COMPUTE",
                        "billing_type": "CONSUMPTION",
                        "is_adjustment": False,
                        "currency": "USD",
                        "spend": 24.0,
                    }
                ]
            return [
                {
                    "usage_date": "2026-06-05",
                    "service_type": "WAREHOUSE_METERING",
                    "rating_type": "COMPUTE",
                    "currency": "USD",
                    "effective_rate": 3.0,
                }
            ]
        return _source_rows(_source_key_for_sql(lowered))

    monkeypatch.setattr("app.services.dashboard_datasets.execute_source_query", execute)

    run_response = TestClient(app).post(
        "/api/dashboard-runs",
        json={
            "organization_id": ORG_ID,
            "source": "snowflake",
            "window_days": 30,
        },
    )
    assert run_response.status_code == 202
    run_id = run_response.json()["id"]
    assert _wait_terminal(run_id) == "completed"

    cached = run_store.get_active(ORG_ID)
    assert cached is not None
    assert cached.run_id == run_id
    assert cached.source == "snowflake"
    assert cached.window_days == FETCH_WINDOW_DAYS
    assert cached.account_locator == "TU24199"
    assert "service_spend_daily" in cached.datasets
    # expires_at = completed_at + ttl (default 24h) since no settings row exists.
    assert cached.expires_at == cached.completed_at + timedelta(seconds=86_400)


def test_snowflake_finalize_does_not_write_cache_when_disabled(
    _stores, monkeypatch
) -> None:
    from tests.test_snowflake_dashboard_run import (
        _source_key_for_sql,
        _source_rows,
        _wait_terminal,
    )

    settings_store, run_store = _stores
    settings_store.upsert(ORG_ID, cache_enabled=False)
    monkeypatch.setenv("DATA_SOURCE", "snowflake")

    def execute(
        sql: str, bind_params: dict[str, Any], config: Any = None
    ) -> list[dict[str, Any]]:
        lowered = sql.lower()
        if "current_account()" in lowered:
            return [{"account_locator": "TU24199"}]
        if "remaining_balance_daily" in lowered:
            return [
                {"usage_date": "2026-06-05", "currency": "USD", "balance": 15_000.0}
            ]
        if "organization_usage" in lowered:
            if "usage_in_currency_daily" in lowered:
                return [
                    {
                        "usage_date": "2026-06-05",
                        "service_type": "WAREHOUSE_METERING",
                        "rating_type": "COMPUTE",
                        "billing_type": "CONSUMPTION",
                        "is_adjustment": False,
                        "currency": "USD",
                        "spend": 24.0,
                    }
                ]
            return [
                {
                    "usage_date": "2026-06-05",
                    "service_type": "WAREHOUSE_METERING",
                    "rating_type": "COMPUTE",
                    "currency": "USD",
                    "effective_rate": 3.0,
                }
            ]
        return _source_rows(_source_key_for_sql(lowered))

    monkeypatch.setattr("app.services.dashboard_datasets.execute_source_query", execute)

    run_response = TestClient(app).post(
        "/api/dashboard-runs",
        json={
            "organization_id": ORG_ID,
            "source": "snowflake",
            "window_days": 30,
        },
    )
    assert run_response.status_code == 202
    run_id = run_response.json()["id"]
    assert _wait_terminal(run_id) == "completed"

    assert run_store.get_active(ORG_ID) is None


def test_cached_returns_204_when_account_locator_stale(_stores) -> None:
    # Cached run belongs to account DEMO123; the org's CURRENT connection is a
    # DIFFERENT account (connection swapped). Serving the cached data would leak
    # the old connection's costs, so this is a MISS.
    _settings_store, run_store = _stores
    _seed_active_cached_run(run_store)

    def _swapped_org() -> AuthContext:
        return AuthContext(
            user_id="actor-1",
            auth_required=True,
            memberships=frozenset({ORG_ID}),
            organizations=(
                Organization(
                    id=ORG_ID, name="Acme", role="member", account_locator="OTHER99"
                ),
            ),
        )

    app.dependency_overrides[require_auth_context] = _swapped_org
    try:
        response = TestClient(app).get(
            f"/api/dashboard-runs/cached?organization_id={ORG_ID}"
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 204


def test_cached_returns_204_when_org_disconnected(_stores) -> None:
    # Org has no current connection (account_locator is None). A cached run with
    # a real locator must not be served.
    _settings_store, run_store = _stores
    _seed_active_cached_run(run_store)

    def _disconnected_org() -> AuthContext:
        return AuthContext(
            user_id="actor-1",
            auth_required=True,
            memberships=frozenset({ORG_ID}),
            organizations=(
                Organization(
                    id=ORG_ID, name="Acme", role="member", account_locator=None
                ),
            ),
        )

    app.dependency_overrides[require_auth_context] = _disconnected_org
    try:
        response = TestClient(app).get(
            f"/api/dashboard-runs/cached?organization_id={ORG_ID}"
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 204


def test_cached_returns_204_when_org_disconnected_and_cache_lacks_fingerprint(
    _stores,
) -> None:
    # Fail closed when both sides are missing a locator: old cache rows can lack
    # the account fingerprint, and a disconnected org also has no active locator.
    # Treating None == None as a hit would serve stale Snowflake data.
    _settings_store, run_store = _stores
    completed_at = _seed_active_cached_run(run_store)
    cached = run_store.get_active(ORG_ID)
    assert cached is not None
    run_store.upsert(
        CachedDashboardRun(
            **{
                **cached.__dict__,
                "account_locator": None,
                "completed_at": completed_at,
            }
        )
    )

    def _disconnected_org() -> AuthContext:
        return AuthContext(
            user_id="actor-1",
            auth_required=True,
            memberships=frozenset({ORG_ID}),
            organizations=(
                Organization(
                    id=ORG_ID, name="Acme", role="member", account_locator=None
                ),
            ),
        )

    app.dependency_overrides[require_auth_context] = _disconnected_org
    try:
        response = TestClient(app).get(
            f"/api/dashboard-runs/cached?organization_id={ORG_ID}"
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 204


def test_cached_returns_204_when_connection_status_is_invalid(_stores) -> None:
    # Disconnect invalidates the connection secret/status, but older membership
    # payloads can still expose the stale account locator. A matching locator
    # must not be enough to serve cached Snowflake data when the connection is
    # not active.
    _settings_store, run_store = _stores
    _seed_active_cached_run(run_store)

    def _invalid_connection_org() -> AuthContext:
        return AuthContext(
            user_id="actor-1",
            auth_required=True,
            memberships=frozenset({ORG_ID}),
            organizations=(
                SimpleNamespace(
                    id=ORG_ID,
                    name="Acme",
                    role="member",
                    account_locator=DEMO_ACCOUNT_LOCATOR,
                    connection_status="invalid",
                ),
            ),
        )

    app.dependency_overrides[require_auth_context] = _invalid_connection_org
    try:
        response = TestClient(app).get(
            f"/api/dashboard-runs/cached?organization_id={ORG_ID}"
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 204


def test_repeated_cached_hits_do_not_accumulate_snapshots(_stores) -> None:
    # N repeated /cached hits for the SAME underlying cached run must retain at
    # most ONE in-memory snapshot (no unbounded UUID-backed snapshot growth),
    # and the returned run's /view must still resolve.
    _settings_store, run_store = _stores
    _seed_active_cached_run(run_store)

    app.dependency_overrides[require_auth_context] = _member_ctx
    try:
        client = TestClient(app)
        run_ids = set()
        for _ in range(5):
            response = client.get(
                f"/api/dashboard-runs/cached?organization_id={ORG_ID}"
            )
            assert response.status_code == 200
            run_ids.add(response.json()["run"]["id"])

        # Same cached run reused across every hit -> one snapshot run_id.
        assert len(run_ids) == 1
        # Only that one snapshot lives in the repo.
        assert len(dashboard_run_repository._runs) == 1

        # The reused run still renders a view off the cached datasets.
        (run_id,) = run_ids
        view_response = client.get(f"/api/dashboard-runs/{run_id}/view?window_days=7")
        assert view_response.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_disconnect_drops_cached_run(_stores, monkeypatch) -> None:
    # Disconnecting keeps the connection row's account_locator, so the /cached
    # fingerprint check would still match and serve stale data. The disconnect
    # must drop the cached row so a later /cached is a miss (204).
    from app.routes import onboarding
    from app.services.membership_directory import Organization

    _settings_store, run_store = _stores
    _seed_active_cached_run(run_store)
    monkeypatch.setattr(onboarding, "disconnect_org_connection", lambda org_id: None)

    def _owner_ctx() -> AuthContext:
        return AuthContext(
            user_id="actor-1",
            auth_required=True,
            memberships=frozenset({ORG_ID}),
            organizations=(
                Organization(
                    id=ORG_ID,
                    name="Acme",
                    role="owner",
                    account_locator=DEMO_ACCOUNT_LOCATOR,
                ),
            ),
        )

    app.dependency_overrides[require_auth_context] = _owner_ctx
    try:
        client = TestClient(app)
        disconnect = client.post(f"/api/onboarding/{ORG_ID}/disconnect")
        assert disconnect.status_code == 204
        assert run_store.get_active(ORG_ID) is None

        cached = client.get(f"/api/dashboard-runs/cached?organization_id={ORG_ID}")
        assert cached.status_code == 204
    finally:
        app.dependency_overrides.clear()


def test_cache_hit_serves_deferred_source_without_refetch(_stores, monkeypatch) -> None:
    # A cached run whose datasets already include the deferred AI source must
    # serve it straight from the cache on a /cached hit — no live Snowflake fetch.
    import app.routes.dashboard_runs as dr

    _settings_store, run_store = _stores
    payload = build_demo_dashboard_dataset()
    # The demo dataset already carries a valid ai_consumption_daily dataset.
    assert "ai_consumption_daily" in payload.datasets
    bounds = _source_bounds_for_dataset_rows(payload.datasets)
    now = datetime.now(timezone.utc)
    run_store.upsert(
        CachedDashboardRun(
            organization_id=ORG_ID,
            run_id="00000000-0000-0000-0000-0000000000aa",
            source="snowflake",
            window_days=payload.run.window_days,
            account_locator=DEMO_ACCOUNT_LOCATOR,
            summary=payload.summary.model_dump(mode="json"),
            metadata=payload.metadata.model_dump(mode="json"),
            datasets=payload.datasets,
            source_start_date=bounds.source_start_date,
            source_end_date=bounds.source_end_date,
            completed_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=23),
        )
    )

    def _boom(*args, **kwargs):
        raise AssertionError("deferred source must not be fetched on a cache hit")

    monkeypatch.setattr(dr, "_run_deferred_source", _boom)

    app.dependency_overrides[require_auth_context] = _member_ctx
    try:
        client = TestClient(app)
        run_id = client.get(
            f"/api/dashboard-runs/cached?organization_id={ORG_ID}"
        ).json()["run"]["id"]
        source = client.get(
            f"/api/dashboard-runs/{run_id}/sources/ai_consumption_daily"
        )
        assert source.status_code == 200
        assert source.json()["status"] == "completed"
    finally:
        app.dependency_overrides.clear()


def test_deferred_trigger_folds_ai_rows_into_cache(_stores, monkeypatch) -> None:
    # Triggering the deferred source on a completed snowflake run persists the AI
    # rows into the org's cache row, so a later /cached hit serves them from cache
    # without a fresh fetch.
    import app.routes.dashboard_runs as dr

    _settings_store, run_store = _stores
    payload = build_demo_dashboard_dataset()
    # Seed the run + cache row WITHOUT the AI dataset so the deferred source can
    # be claimed and actually fetched (a present AI source would already be
    # "completed" and short-circuit the trigger).
    datasets_no_ai = {
        k: v for k, v in payload.datasets.items() if k != "ai_consumption_daily"
    }

    # Create a completed snowflake run in the repo whose id matches the cache row.
    run = dashboard_run_repository.create_completed_snapshot(
        organization_id=UUID(ORG_ID),
        source="snowflake",
        window_days=payload.run.window_days,
        summary=payload.summary.model_dump(mode="json"),
        datasets=datasets_no_ai,
        metadata=payload.metadata.model_dump(mode="json"),
        retention_days=1,
    )
    bounds = _source_bounds_for_dataset_rows(datasets_no_ai)
    now = datetime.now(timezone.utc)
    run_store.upsert(
        CachedDashboardRun(
            organization_id=ORG_ID,
            run_id=run.id,
            source="snowflake",
            window_days=payload.run.window_days,
            account_locator=DEMO_ACCOUNT_LOCATOR,
            summary=payload.summary.model_dump(mode="json"),
            metadata=payload.metadata.model_dump(mode="json"),
            datasets=datasets_no_ai,
            source_start_date=bounds.source_start_date,
            source_end_date=bounds.source_end_date,
            completed_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=23),
        )
    )

    ai_rows = payload.datasets["ai_consumption_daily"]
    monkeypatch.setattr(dr, "_run_deferred_source", lambda *a, **k: (ai_rows, []))

    app.dependency_overrides[require_auth_context] = _member_ctx
    try:
        client = TestClient(app)
        trigger = client.post(
            f"/api/dashboard-runs/{run.id}/sources/ai_consumption_daily"
        )
        assert trigger.status_code == 202
    finally:
        app.dependency_overrides.clear()

    cached = run_store.get_active(ORG_ID)
    assert cached is not None
    assert cached.datasets.get("ai_consumption_daily") == ai_rows


def test_deferred_cache_fold_does_not_overwrite_newer_cached_run(
    _stores, monkeypatch
) -> None:
    import app.routes.dashboard_runs as dr

    _settings_store, run_store = _stores
    payload = build_demo_dashboard_dataset()
    datasets_no_ai = {
        k: v for k, v in payload.datasets.items() if k != "ai_consumption_daily"
    }
    run = dashboard_run_repository.create_completed_snapshot(
        organization_id=UUID(ORG_ID),
        source="snowflake",
        window_days=payload.run.window_days,
        summary=payload.summary.model_dump(mode="json"),
        datasets=datasets_no_ai,
        metadata=payload.metadata.model_dump(mode="json"),
        retention_days=1,
    )
    bounds = _source_bounds_for_dataset_rows(datasets_no_ai)
    now = datetime.now(timezone.utc)
    older_cached = CachedDashboardRun(
        organization_id=ORG_ID,
        run_id=run.id,
        source="snowflake",
        window_days=payload.run.window_days,
        account_locator=DEMO_ACCOUNT_LOCATOR,
        summary=payload.summary.model_dump(mode="json"),
        metadata=payload.metadata.model_dump(mode="json"),
        datasets=datasets_no_ai,
        source_start_date=bounds.source_start_date,
        source_end_date=bounds.source_end_date,
        completed_at=now - timedelta(hours=2),
        expires_at=now + timedelta(hours=22),
    )
    newer_cached = replace(
        older_cached,
        run_id="00000000-0000-0000-0000-0000000000bb",
        completed_at=now - timedelta(minutes=5),
    )
    run_store.upsert(older_cached)

    def stale_read(organization_id: str, *, now: datetime | None = None):
        run_store._rows[organization_id] = newer_cached
        return older_cached

    monkeypatch.setattr(run_store, "get_active", stale_read)

    dr._update_cached_run_datasets(
        run, "ai_consumption_daily", payload.datasets["ai_consumption_daily"]
    )

    cached = run_store._rows.get(ORG_ID)
    assert cached is not None
    assert cached.run_id == newer_cached.run_id
    assert "ai_consumption_daily" not in cached.datasets


def test_demo_run_never_writes_cache(_stores) -> None:
    _settings_store, run_store = _stores
    # Demo mode (default DATA_SOURCE=demo, auth not required). The create source
    # is always "snowflake"; DATA_SOURCE=demo routes it to the demo builder,
    # which must never touch the cache.
    response = TestClient(app).post(
        "/api/dashboard-runs",
        json={"organization_id": ORG_ID, "source": "snowflake", "window_days": 30},
    )
    assert response.status_code == 201
    assert run_store.get_active(ORG_ID) is None

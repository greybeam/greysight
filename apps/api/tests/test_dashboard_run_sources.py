from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes.dashboard_runs import (
    InMemoryDashboardRunRepository,
    dashboard_run_repository,
)


def _repo_with_run():
    repo = InMemoryDashboardRunRepository()
    run = repo.create_completed_snapshot(
        organization_id=None,
        source="snowflake",
        window_days=30,
        summary={},
        datasets={"rate_sheet_daily": [{"usage_date": "2026-06-01"}]},
        retention_days=7,
    )
    from uuid import UUID

    return repo, UUID(run.id)


def test_claim_source_dedupes_concurrent_starts():
    repo, run_id = _repo_with_run()
    assert repo.claim_source(run_id, "ai_consumption_daily") is True
    # second claim while pending is rejected
    assert repo.claim_source(run_id, "ai_consumption_daily") is False
    assert repo.get_source_state(run_id, "ai_consumption_daily") == "pending"


def test_complete_source_appends_dataset_and_inherits_expiry():
    repo, run_id = _repo_with_run()
    repo.claim_source(run_id, "ai_consumption_daily")
    repo.complete_source(
        run_id,
        "ai_consumption_daily",
        rows=[
            {"usage_date": "2026-06-01", "consumption_type": "X", "credits_used": 1.0}
        ],
        partial=True,
        skipped_branches=["cortex_code_cli"],
    )
    assert repo.get_source_state(run_id, "ai_consumption_daily") == "completed"
    inputs = repo.get_view_inputs(run_id)
    assert inputs is not None
    _run, datasets, _meta, _bounds, _statuses, _auth = inputs
    assert "ai_consumption_daily" in datasets


def test_fail_source_sets_failed_state():
    repo, run_id = _repo_with_run()
    repo.claim_source(run_id, "ai_consumption_daily")
    repo.fail_source(run_id, "ai_consumption_daily")
    assert repo.get_source_state(run_id, "ai_consumption_daily") == "failed"
    # a failed source can be retried
    assert repo.claim_source(run_id, "ai_consumption_daily") is True


def test_claim_rejects_missing_or_deleted_run():
    repo, run_id = _repo_with_run()
    assert repo.claim_source(uuid4(), "ai_consumption_daily") is False
    repo.delete_run(run_id)
    assert repo.claim_source(run_id, "ai_consumption_daily") is False


def test_claim_rejects_expired_run_and_clears_stale_source_state():
    # Regression: expiry is lazy. A still-"completed" run whose datasets have
    # aged out must not be claimable, and a previously "completed" source state
    # must not survive the data it described.
    repo, run_id = _repo_with_run()
    repo.claim_source(run_id, "ai_consumption_daily")
    repo.complete_source(
        run_id,
        "ai_consumption_daily",
        rows=[
            {"usage_date": "2026-06-01", "consumption_type": "X", "credits_used": 1.0}
        ],
        partial=False,
        skipped_branches=[],
    )
    assert repo.get_source_state(run_id, "ai_consumption_daily") == "completed"

    repo.expire_run_datasets(run_id)

    assert repo.claim_source(run_id, "ai_consumption_daily") is False
    assert repo.get_source_state(run_id, "ai_consumption_daily") != "completed"
    assert repo.get_source_state(run_id, "ai_consumption_daily") is None
    assert repo.get_run(run_id).status == "expired"


_AI_ROWS = [
    {
        "usage_date": "2026-06-05",
        "service_type": "CORTEX_SEARCH",
        "consumption_type": "CORTEX_SEARCH",
        "credits_used": 4.0,
    },
    {
        "usage_date": "2026-06-06",
        "service_type": "AI_INFERENCE",
        "consumption_type": "AI_INFERENCE",
        "credits_used": 2.0,
    },
]


@pytest.fixture
def client():
    dashboard_run_repository.clear()
    yield TestClient(app)
    dashboard_run_repository.clear()


@pytest.fixture
def client_with_completed_run(monkeypatch):
    dashboard_run_repository.clear()
    run = dashboard_run_repository.create_completed_snapshot(
        organization_id=UUID("00000000-0000-0000-0000-000000000001"),
        source="snowflake",
        window_days=100,
        summary={},
        datasets={
            "rate_sheet_daily": [],
            "service_spend_daily": [
                # Span a wide enough window that a 30-day relative range fits
                # within the source bounds (bounds derive from usage dates).
                {
                    "usage_date": "2026-03-01",
                    "service_type": "CORTEX_SEARCH",
                    "credits_used": 1.0,
                },
                {
                    "usage_date": "2026-06-06",
                    "service_type": "CORTEX_SEARCH",
                    "credits_used": 4.0,
                },
            ],
        },
        metadata={
            "data_mode": "estimated",
            "account_locator": "TU24199",
            "currency": "USD",
            "billing_through_date": None,
            "account_usage_through_date": "2026-06-06",
            "estimated_credit_price_usd": 3.0,
            "storage_price_usd_per_tb_month": 23.0,
            "unsupported_reason": None,
            "organization_usage": {"available": False},
            "account_usage": {"available": True},
        },
        retention_days=7,
    )

    # Stub the AI-branch fetch: every deferred execute call returns canned AI
    # rows. The route's execute closure resolves execute_source_query through
    # app.services.dashboard_datasets, mirroring the main-run executor path.
    def fake_execute(sql, bind_params, config=None):
        return _AI_ROWS

    monkeypatch.setattr(
        "app.services.dashboard_datasets.execute_source_query", fake_execute
    )

    yield TestClient(app), UUID(run.id)
    dashboard_run_repository.clear()


def test_post_source_then_get_returns_detail_view(client_with_completed_run):
    client, run_id = client_with_completed_run
    posted = client.post(f"/api/dashboard-runs/{run_id}/sources/ai_consumption_daily")
    assert posted.status_code in (200, 202)

    got = client.get(
        f"/api/dashboard-runs/{run_id}/sources/ai_consumption_daily?window_days=30"
    )
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "completed"
    assert "view" in body and "daily_series" in body["view"]


def test_get_unknown_source_404(client_with_completed_run):
    client, run_id = client_with_completed_run
    r = client.get(f"/api/dashboard-runs/{run_id}/sources/not_a_source")
    assert r.status_code == 404


def test_post_source_on_missing_run_404(client):
    r = client.post(f"/api/dashboard-runs/{uuid4()}/sources/ai_consumption_daily")
    assert r.status_code == 404


def test_get_before_post_reports_idle(client_with_completed_run):
    client, run_id = client_with_completed_run
    r = client.get(f"/api/dashboard-runs/{run_id}/sources/ai_consumption_daily")
    assert r.status_code == 200
    assert r.json()["status"] in ("idle", "pending")

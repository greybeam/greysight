from uuid import uuid4

import pytest

from app.models import DashboardRunCreateRequest
from app.routes.dashboard_runs import InMemoryDashboardRunRepository


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
        rows=[{"usage_date": "2026-06-01", "consumption_type": "X", "credits_used": 1.0}],
        partial=True,
        skipped_branches=["cortex_code_cli"],
    )
    assert repo.get_source_state(run_id, "ai_consumption_daily") == "completed"
    inputs = repo.get_view_inputs(run_id)
    assert inputs is not None
    _run, datasets, _meta, _bounds = inputs
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

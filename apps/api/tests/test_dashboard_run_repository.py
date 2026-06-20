from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

import app.routes.dashboard_runs as dr
from app.routes.dashboard_runs import (
    BASE_RUN_SOURCE_KEYS,
    InMemoryDashboardRunRepository,
)


def _new_running_repo() -> tuple[InMemoryDashboardRunRepository, UUID]:
    repo = InMemoryDashboardRunRepository()
    run = repo.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    return repo, UUID(run.id)


def test_create_running_run_seeds_pending_sources():
    repo, run_id = _new_running_repo()
    run = repo.get_run(run_id)
    assert run is not None and run.status == "running"
    statuses = _source_statuses_from_existing_records(repo, run_id)
    assert set(statuses) == set(BASE_RUN_SOURCE_KEYS)
    assert all(s == "pending" for s in statuses.values())


def test_set_dataset_and_mark_ready_updates_view_inputs():
    repo, run_id = _new_running_repo()
    repo.set_dataset(run_id, "service_spend_daily", [{"usage_date": "2026-06-01"}])
    repo.complete_source(run_id, "service_spend_daily")
    assert _source_statuses_from_existing_records(repo, run_id)[
        "service_spend_daily"
    ] == "ready"
    view_inputs = repo.get_view_inputs(run_id)
    assert view_inputs is not None
    _run, datasets, _metadata, bounds, _statuses, _auth = view_inputs
    assert datasets["service_spend_daily"] == [{"usage_date": "2026-06-01"}]
    # provisional bounds reflect the only landed usage_date
    assert bounds.source_start_date.isoformat() == "2026-06-01"


def test_fail_source_marks_base_source_unavailable():
    repo, run_id = _new_running_repo()
    repo.fail_source(run_id, "capacity_balance_daily", error="unavailable")
    assert _source_statuses_from_existing_records(repo, run_id)[
        "capacity_balance_daily"
    ] == "unavailable"


def test_finalize_run_sets_completed_and_authoritative_bounds():
    repo, run_id = _new_running_repo()
    repo.finalize_run(
        run_id,
        status="completed",
        summary={"total_credits": 1.0},
        metadata=None,
        datasets={"service_spend_daily": [{"usage_date": "2026-05-01"}]},
    )
    run = repo.get_run(run_id)
    assert run is not None and run.status == "completed"
    view_inputs = repo.get_view_inputs(run_id)
    assert view_inputs is not None
    _run, datasets, _meta, bounds, _statuses, _auth = view_inputs
    assert datasets["service_spend_daily"] == [{"usage_date": "2026-05-01"}]
    assert bounds.source_end_date.isoformat() == "2026-05-01"


def test_finalize_run_is_atomic_on_bounds_failure(monkeypatch):
    """Finding 3: if dataset/bounds construction inside finalize_run raises, the
    run must remain "running" (status not mutated to the terminal value), so the
    worker's fallback finalize_run(status="failed") still lands the run terminal
    instead of no-opping at the _is_running_locked guard.
    """
    repo, run_id = _new_running_repo()

    def boom(*_a, **_k):
        raise RuntimeError("bounds computation exploded")

    monkeypatch.setattr(dr, "_source_bounds_for_dataset_rows", boom)

    # (a) the exception propagates.
    with pytest.raises(RuntimeError, match="bounds computation exploded"):
        repo.finalize_run(
            run_id,
            status="completed",
            summary={"total_credits": 1.0},
            metadata=None,
            datasets={"service_spend_daily": [{"usage_date": "2026-05-01"}]},
        )

    # (b) the run is STILL "running" — status was not half-mutated.
    run = repo.get_run(run_id)
    assert run is not None and run.status == "running"

    # (c) the fallback failed-finalize still succeeds and lands "failed".
    monkeypatch.undo()
    repo.finalize_run(
        run_id,
        status="failed",
        summary={},
        metadata=None,
        datasets={},
        error="boom",
    )
    run = repo.get_run(run_id)
    assert run is not None and run.status == "failed"


def test_writes_after_terminal_state_are_discarded():
    repo, run_id = _new_running_repo()
    repo.finalize_run(run_id, status="completed", summary={}, metadata=None,
                      datasets={"service_spend_daily": [{"usage_date": "2026-05-01"}]})
    # A late worker write must not mutate the finalized run.
    repo.set_dataset(run_id, "service_spend_daily", [{"usage_date": "1999-01-01"}])
    repo.complete_source(run_id, "service_spend_daily")
    _run, datasets, _m, _b, _s, _auth = repo.get_view_inputs(run_id)
    assert datasets["service_spend_daily"] == [{"usage_date": "2026-05-01"}]


def test_finalize_rewraps_preserved_deferred_dataset_expiry():
    """A deferred source that completes before any base dataset lands gets
    complete_source's hard-coded 7-day fallback expiry. finalize_run must
    re-wrap that preserved deferred dataset with the run's real (longer)
    retention so the whole run does not expire early — expiry checks fail on
    ANY expired dataset.
    """
    repo = InMemoryDashboardRunRepository()
    run = repo.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=BASE_RUN_SOURCE_KEYS,
        retention_days=30,
    )
    run_id = UUID(run.id)

    # Deferred AI source completes while the base run is still running and
    # BEFORE any base dataset has landed -> 7-day fallback expiry.
    repo.complete_source(
        run_id,
        "ai_consumption_daily",
        rows=[{"usage_date": "2026-06-01", "credits_used": 1.0}],
    )
    deferred_before = repo._datasets[run_id]["ai_consumption_daily"]
    assert deferred_before.retention_expires_at < datetime.now(
        timezone.utc
    ) + timedelta(days=8)

    repo.finalize_run(
        run_id,
        status="completed",
        summary={},
        metadata=None,
        datasets={"service_spend_daily": [{"usage_date": "2026-05-01"}]},
    )

    base_expiry = repo._datasets[run_id]["service_spend_daily"].retention_expires_at
    deferred_expiry = repo._datasets[run_id][
        "ai_consumption_daily"
    ].retention_expires_at
    # Preserved deferred dataset now shares the final run expiry, not the
    # stale 7-day fallback.
    assert deferred_expiry == base_expiry
    assert deferred_expiry > datetime.now(timezone.utc) + timedelta(days=8)
    # The deferred rows are still preserved (not wiped by the base datasets).
    assert repo._datasets[run_id]["ai_consumption_daily"].aggregate_dataset == [
        {"usage_date": "2026-06-01", "credits_used": 1.0}
    ]


def test_running_run_ttl_auto_expires(monkeypatch):
    repo, run_id = _new_running_repo()
    # Force the deadline into the past, then read the run.
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    with repo._lock:
        repo._running_deadlines[run_id] = past
    run = repo.get_run(run_id)
    assert run is not None and run.status == "expired"


def _source_statuses_from_existing_records(
    repo: InMemoryDashboardRunRepository, run_id: UUID
) -> dict[str, str]:
    """Use the repo's existing deferred-source record read path in real tests."""
    return {
        key: repo.get_source(run_id, key).status
        for key in BASE_RUN_SOURCE_KEYS
    }

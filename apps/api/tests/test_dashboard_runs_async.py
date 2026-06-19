import time
from uuid import UUID

import app.routes.dashboard_runs as dr
from app.config import Settings
from app.services.parallel_source_runner import SourceOutcome


def _wait_terminal(run_id: UUID, timeout: float = 2.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = dr.dashboard_run_repository.get_run(run_id)
        if run is not None and run.status in {"completed", "failed", "expired"}:
            return run.status
        time.sleep(0.02)
    raise AssertionError("worker never reached a terminal state")


class _Data:
    summary = {"total_credits": 0.0}

    class metadata:  # noqa: N801
        @staticmethod
        def model_dump(mode="json"):
            return None

    datasets = {"service_spend_daily": [{"usage_date": "2026-05-01"}]}


def test_worker_reaches_completed_and_streams_datasets(monkeypatch):
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)

    def fake_build(
        settings, *, summary_window_days, connection_config, on_source_outcome=None
    ):
        if on_source_outcome is not None:
            on_source_outcome(
                SourceOutcome(
                    key="service_spend_daily",
                    rows=[{"usage_date": "2026-05-01"}],
                    available=True,
                )
            )
        return _Data()

    monkeypatch.setattr(dr, "build_snowflake_dashboard_data", fake_build)
    dr._run_dashboard_worker(run_id, Settings(), object(), 30)
    assert _wait_terminal(run_id) == "completed"
    assert (
        dr.dashboard_run_repository.get_source(run_id, "service_spend_daily").status
        == "ready"
    )


def test_worker_unhandled_exception_finalizes_failed(monkeypatch):
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)

    def boom(*a, **k):
        raise RuntimeError("snowflake exploded")

    monkeypatch.setattr(dr, "build_snowflake_dashboard_data", boom)
    dr._run_dashboard_worker(run_id, Settings(), object(), 30)
    assert _wait_terminal(run_id) == "failed"


def test_worker_sources_unavailable_finalizes_failed_with_safe_message(monkeypatch):
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)

    def unavailable(*a, **k):
        raise dr.DashboardSourcesUnavailableError("raw backend detail")

    monkeypatch.setattr(dr, "build_snowflake_dashboard_data", unavailable)
    dr._run_dashboard_worker(run_id, Settings(), object(), 30)
    assert _wait_terminal(run_id) == "failed"
    final = dr.dashboard_run_repository.get_run(run_id)
    assert final.error == (
        "Could not query Snowflake billing or Account Usage data."
    )


def test_base_complete_source_is_noop_on_terminal_run():
    """A slow base source landing after the run is finalized must not error or
    resurrect/mutate the terminal run."""
    repo = dr.InMemoryDashboardRunRepository()
    run = repo.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)
    repo.finalize_run(
        run_id,
        status="completed",
        summary={},
        metadata=None,
        datasets={"service_spend_daily": [{"usage_date": "2026-05-01"}]},
    )
    before = repo.get_source(run_id, "service_spend_daily").status

    # Late base-source landings on a terminal run are no-ops.
    repo.set_dataset(run_id, "service_spend_daily", [{"usage_date": "1999-01-01"}])
    repo.complete_source(run_id, "service_spend_daily")
    repo.fail_source(run_id, "warehouse_spend_daily", error="too late")

    # Source state for base sources is unchanged; datasets not mutated.
    assert repo.get_source(run_id, "service_spend_daily").status == before
    _r, datasets, _m, _b = repo.get_view_inputs(run_id)
    assert datasets["service_spend_daily"] == [{"usage_date": "2026-05-01"}]


def test_deferred_ai_source_still_updates_after_completion():
    """The deferred-AI post-completion lifecycle must keep working: an AI source
    legitimately completes AFTER the base run is completed."""
    repo = dr.InMemoryDashboardRunRepository()
    run = repo.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)
    repo.finalize_run(
        run_id,
        status="completed",
        summary={},
        metadata=None,
        datasets={"service_spend_daily": [{"usage_date": "2026-05-01"}]},
    )

    # Deferred AI source claims + completes after the run is completed.
    assert repo.claim_source(run_id, "ai_consumption_daily") is True
    repo.complete_source(
        run_id, "ai_consumption_daily", rows=[{"usage_date": "2026-05-02"}]
    )
    assert repo.get_source(run_id, "ai_consumption_daily").status == "completed"
    _r, datasets, _m, _b = repo.get_view_inputs(run_id)
    assert datasets["ai_consumption_daily"] == [{"usage_date": "2026-05-02"}]

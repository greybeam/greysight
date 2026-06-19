import time
from uuid import UUID

import app.routes.dashboard_runs as dr
from app.auth import AuthContext
from app.config import Settings
from app.routes.dashboard_runs import read_dashboard_run_view
from app.services.parallel_source_runner import SourceOutcome


def _anon() -> AuthContext:
    """Auth-disabled context for direct route-function calls in these tests."""
    return AuthContext(user_id=None, auth_required=False)


def _all_dep_sources() -> set[str]:
    return {s for deps in dr.SECTION_SOURCE_DEPENDENCIES.values() for s in deps}


def test_sections_are_overview_warehouse_storage():
    assert set(dr.SECTION_SOURCE_DEPENDENCIES) == {"overview", "warehouse", "storage"}


def test_section_ready_only_when_all_deps_ready():
    all_ready = {key: "ready" for key in _all_dep_sources()}
    statuses = dr.compute_section_statuses(all_ready)
    assert statuses == {
        "overview": "ready",
        "warehouse": "ready",
        "storage": "ready",
    }


def test_pending_dep_keeps_section_pending():
    statuses = dr.compute_section_statuses({"warehouse_spend_daily": "pending"})
    assert statuses["warehouse"] == "pending"


def test_unavailable_dep_marks_section_unavailable():
    base = {key: "ready" for key in _all_dep_sources()}
    base["database_storage_daily"] = "unavailable"
    statuses = dr.compute_section_statuses(base)
    assert statuses["storage"] == "unavailable"


def test_failed_dep_marks_section_unavailable():
    base = {key: "ready" for key in _all_dep_sources()}
    base["service_spend_daily"] = "failed"
    statuses = dr.compute_section_statuses(base)
    assert statuses["overview"] == "unavailable"


def test_missing_dep_defaults_section_to_pending():
    # An empty source-status map (nothing landed yet) keeps every section pending.
    statuses = dr.compute_section_statuses({})
    assert statuses == {
        "overview": "pending",
        "warehouse": "pending",
        "storage": "pending",
    }


def test_running_view_reports_section_statuses():
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)
    # Only warehouse data has landed.
    dr.dashboard_run_repository.set_dataset(
        run_id,
        "warehouse_spend_daily",
        [
            {
                "usage_date": "2026-06-01",
                "warehouse_name": "WH",
                "credits_used": 1.0,
                "credits_used_compute": 1.0,
            }
        ],
    )
    dr.dashboard_run_repository.complete_source(run_id, "warehouse_spend_daily")

    payload = read_dashboard_run_view(run_id, None, None, None, _anon())
    assert payload["run"]["status"] == "running"
    assert payload["section_statuses"]["warehouse"] == "ready"
    assert payload["section_statuses"]["overview"] == "pending"
    assert payload["section_statuses"]["storage"] == "pending"


def test_running_view_with_no_landed_data_keeps_all_pending():
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)

    payload = read_dashboard_run_view(run_id, None, None, None, _anon())
    assert payload["run"]["status"] == "running"
    assert payload["section_statuses"] == {
        "overview": "pending",
        "warehouse": "pending",
        "storage": "pending",
    }


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


def test_completed_snapshot_all_datasets_present_yields_ready_section_statuses():
    """Regression: create_completed_snapshot must seed _source_states so that
    read_dashboard_run_view returns section_statuses = {all: ready} when all
    base datasets are present — not all-pending (the pre-fix bug).

    Empty lists are intentional: an empty dataset IS present in the snapshot
    (key exists in the dict), so it should produce status=ready, not pending.
    The view builder handles empty lists gracefully (renders empty charts).
    """
    dr.dashboard_run_repository.clear()
    # All base keys present; empty lists so the view builder has no rows to
    # validate (avoiding required-field errors on stub data).
    all_datasets: dict = {key: [] for key in dr.BASE_RUN_SOURCE_KEYS}
    run = dr.dashboard_run_repository.create_completed_snapshot(
        organization_id=None,
        source="snowflake",
        window_days=30,
        summary={},
        datasets=all_datasets,
        retention_days=7,
    )
    run_id = UUID(run.id)

    payload = read_dashboard_run_view(run_id, None, None, None, _anon())
    assert payload["run"]["status"] == "completed"
    assert payload["section_statuses"] == {
        "overview": "ready",
        "warehouse": "ready",
        "storage": "ready",
    }, (
        "All base datasets present — expected all sections ready; "
        f"got {payload['section_statuses']!r}"
    )


def test_completed_snapshot_missing_storage_dataset_yields_unavailable_storage():
    """Regression: a completed snapshot missing database_storage_daily should
    report storage=unavailable while other present sources remain ready."""
    dr.dashboard_run_repository.clear()
    # Omit database_storage_daily (the storage section dependency).
    partial_datasets: dict = {
        key: []
        for key in dr.BASE_RUN_SOURCE_KEYS
        if key != "database_storage_daily"
    }
    run = dr.dashboard_run_repository.create_completed_snapshot(
        organization_id=None,
        source="snowflake",
        window_days=30,
        summary={},
        datasets=partial_datasets,
        retention_days=7,
    )
    run_id = UUID(run.id)

    payload = read_dashboard_run_view(run_id, None, None, None, _anon())
    assert payload["run"]["status"] == "completed"
    section = payload["section_statuses"]
    assert section["storage"] == "unavailable", (
        f"Expected storage=unavailable, got {section['storage']!r}"
    )
    assert section["overview"] == "ready", (
        f"Expected overview=ready, got {section['overview']!r}"
    )
    assert section["warehouse"] == "ready", (
        f"Expected warehouse=ready, got {section['warehouse']!r}"
    )


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

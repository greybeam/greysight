import time
from datetime import date, datetime, timezone
from uuid import UUID

import app.routes.dashboard_runs as dr
from app.auth import AuthContext
from app.config import Settings
from app.models import DashboardDatasetMetadata, SourceAvailability
from app.routes.dashboard_runs import read_dashboard_run_view
from app.services.parallel_source_runner import SourceOutcome


def _anon() -> AuthContext:
    """Auth-disabled context for direct route-function calls in these tests."""
    return AuthContext(user_id=None, auth_required=False)


def _completed_metadata(*, account_usage_available: bool) -> dict:
    """Minimal completed-snapshot metadata dict for direct snapshot seeding.

    ``account_usage_available`` is the authoritative group-collapse signal the
    completed /view branch reconciles against (False => account-usage group
    collapsed => its gating sections roll up "unavailable").
    """
    settings = Settings()
    # Empty gating datasets => source bounds collapse to [today, today]; pin the
    # through_date to today so the requested [today, today] range is in-bounds.
    today = datetime.now(timezone.utc).date()
    return DashboardDatasetMetadata(
        data_mode="estimated",
        account_locator="abc12345",
        currency="USD",
        billing_through_date=None,
        account_usage_through_date=today,
        estimated_credit_price_usd=settings.estimated_credit_price_usd,
        storage_price_usd_per_tb_month=settings.storage_price_usd_per_tb_month,
        organization_usage=SourceAvailability(available=False),
        account_usage=SourceAvailability(available=account_usage_available),
    ).model_dump(mode="json")


def _storage_rows() -> list[dict]:
    return [
        {
            "usage_date": "2026-05-01",
            "database_name": "RAW",
            "average_database_bytes": 1_000_000_000_000,
            "average_failsafe_bytes": 0,
        }
    ]


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


def test_worker_success_finalize_crash_still_finalizes_failed(monkeypatch):
    """T9: a crash in the SUCCESS-path finalize (e.g. metadata.model_dump or
    finalize_run) must still drive the run terminal (failed) with a neutral
    user-facing message — never strand it "running" until the TTL."""
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)

    class _CrashingData:
        summary = {"total_credits": 0.0}

        class metadata:  # noqa: N801
            @staticmethod
            def model_dump(mode="json"):
                raise RuntimeError("raw backend detail in finalize")

        datasets = {"service_spend_daily": [{"usage_date": "2026-05-01"}]}

    def fake_build(*a, **k):
        return _CrashingData()

    monkeypatch.setattr(dr, "build_snowflake_dashboard_data", fake_build)
    dr._run_dashboard_worker(run_id, Settings(), object(), 30)
    assert _wait_terminal(run_id) == "failed"
    final = dr.dashboard_run_repository.get_run(run_id)
    # Neutral message only — the raw backend detail must not leak.
    assert final.error == (
        "An unexpected error occurred while building the dashboard."
    )
    assert "raw backend detail" not in (final.error or "")


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
    _r, datasets, _m, _b, _s, _auth = repo.get_view_inputs(run_id)
    assert datasets["service_spend_daily"] == [{"usage_date": "2026-05-01"}]


def test_completed_snapshot_all_datasets_present_yields_ready_section_statuses():
    """Regression: create_completed_snapshot must seed _source_states so that
    read_dashboard_run_view rolls up section_statuses from final readiness —
    not all-pending (the pre-fix bug).

    Original intent (restored): every gating source is PRESENT and the
    account-usage group is AVAILABLE, so every section is "ready" — even though
    the gating datasets are EMPTY (a legitimately empty time window must NOT
    surface as "unavailable"). The completed-view branch now reconciles against
    GROUP availability (metadata.account_usage.available), not dataset emptiness.
    """
    dr.dashboard_run_repository.clear()
    # All gating datasets present-but-empty (empty window), group AVAILABLE.
    all_datasets: dict = {key: [] for key in dr.BASE_RUN_SOURCE_KEYS}
    run = dr.dashboard_run_repository.create_completed_snapshot(
        organization_id=None,
        source="snowflake",
        window_days=30,
        summary={},
        datasets=all_datasets,
        metadata=_completed_metadata(account_usage_available=True),
        retention_days=7,
    )
    run_id = UUID(run.id)

    # No gating rows => bounds collapse to [today, today]; request that day.
    bounds = dr.dashboard_run_repository.get_source_bounds(run_id)
    payload = read_dashboard_run_view(
        run_id, None, bounds.source_start_date, bounds.source_end_date, _anon()
    )
    assert payload["run"]["status"] == "completed"
    assert payload["section_statuses"] == {
        "overview": "ready",
        "warehouse": "ready",
        "storage": "ready",
    }, (
        "Account-usage group available (empty window) — expected all sections "
        f"ready; got {payload['section_statuses']!r}"
    )


def test_completed_snapshot_collapsed_group_yields_unavailable_sections():
    """A completed snowflake run whose account-usage group COLLAPSED
    (metadata.account_usage.available is False, e.g. _group_from_outcomes zeroed
    the group because one member failed) must report every account-usage gating
    section "unavailable" — even though each source's seeded state is "ready".
    The collapse signal is the metadata availability flag, not dataset
    emptiness."""
    dr.dashboard_run_repository.clear()
    # Datasets present-but-empty; group flag is the authoritative collapse signal.
    datasets: dict = {key: [] for key in dr.BASE_RUN_SOURCE_KEYS}
    run = dr.dashboard_run_repository.create_completed_snapshot(
        organization_id=None,
        source="snowflake",
        window_days=30,
        summary={},
        datasets=datasets,
        metadata=_completed_metadata(account_usage_available=False),
        retention_days=7,
    )
    run_id = UUID(run.id)

    # No gating rows => bounds collapse to [today, today]; request that day.
    bounds = dr.dashboard_run_repository.get_source_bounds(run_id)
    payload = read_dashboard_run_view(
        run_id, None, bounds.source_start_date, bounds.source_end_date, _anon()
    )
    section = payload["section_statuses"]
    assert section["overview"] == "unavailable", section
    assert section["warehouse"] == "unavailable", section
    assert section["storage"] == "unavailable", section


def test_completed_snapshot_empty_window_group_available_yields_ready():
    """A gating source that succeeded but had NO rows in the window (empty
    dataset) with the account-usage group AVAILABLE must keep its section
    "ready" — an empty window is not a collapse. This guards against regressing
    to the old dataset-emptiness heuristic."""
    dr.dashboard_run_repository.clear()
    # storage has rows; overview/warehouse have empty windows. Group available.
    datasets: dict = {key: [] for key in dr.BASE_RUN_SOURCE_KEYS}
    datasets["database_storage_daily"] = _storage_rows()
    run = dr.dashboard_run_repository.create_completed_snapshot(
        organization_id=None,
        source="snowflake",
        window_days=30,
        summary={},
        datasets=datasets,
        metadata=_completed_metadata(account_usage_available=True),
        retention_days=7,
    )
    run_id = UUID(run.id)

    payload = read_dashboard_run_view(
        run_id, None, date(2026, 5, 1), date(2026, 5, 1), _anon()
    )
    assert payload["run"]["status"] == "completed"
    section = payload["section_statuses"]
    assert section["storage"] == "ready", section
    assert section["overview"] == "ready", (
        f"Empty window must stay ready, got {section['overview']!r}"
    )
    assert section["warehouse"] == "ready", (
        f"Empty window must stay ready, got {section['warehouse']!r}"
    )


def test_deferred_source_completes_completed_while_base_run_running():
    """F-IMP-2: a deferred (non-base) source finishing while the base run is
    still "running" must be stamped "completed" (not "ready"), so its
    GET /sources/{id} poll reaches a served/terminal state instead of degrading.
    Base sources keep their "ready while running" semantics."""
    repo = dr.InMemoryDashboardRunRepository()
    run = repo.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)

    # Base source streaming during running -> "ready".
    repo.set_dataset(run_id, "service_spend_daily", [{"usage_date": "2026-05-01"}])
    repo.complete_source(run_id, "service_spend_daily")
    assert repo.get_source(run_id, "service_spend_daily").status == "ready"

    # Deferred (non-base) source completing during running -> "completed".
    assert "ai_consumption_daily" not in dr.BASE_RUN_SOURCE_KEYS
    repo.complete_source(
        run_id, "ai_consumption_daily", rows=[{"usage_date": "2026-05-02"}]
    )
    assert repo.get_run(run_id).status == "running"
    assert repo.get_source_state(run_id, "ai_consumption_daily") == "completed"
    # The served-view inputs include the deferred rows.
    _r, datasets, _m, _b, _s, _auth = repo.get_view_inputs(run_id)
    assert datasets["ai_consumption_daily"] == [{"usage_date": "2026-05-02"}]


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
    _r, datasets, _m, _b, _s, _auth = repo.get_view_inputs(run_id)
    assert datasets["ai_consumption_daily"] == [{"usage_date": "2026-05-02"}]


# --- Focused unit tests for _reconcile_completed_source_statuses --------------
# The completed-view section rollup is reconciled against finalized GROUP
# availability (GATING_SOURCE_GROUP -> account_usage / organization_usage),
# NOT dataset emptiness. These cover the helper directly so the rule is proven
# without constructing full view state.


def _meta(*, account_usage_available: bool) -> dict:
    """Minimal metadata dict shaped like DashboardDatasetMetadata.model_dump()."""
    return {
        "account_usage": {"available": account_usage_available, "detail": None},
        "organization_usage": {"available": True, "detail": None},
    }


def test_reconcile_group_available_keeps_gating_ready():
    """(a) Group available + a gating source streamed "ready" => stays "ready"."""
    statuses = {"service_spend_daily": "ready", "warehouse_spend_daily": "ready"}
    result = dr._reconcile_completed_source_statuses(
        statuses, _meta(account_usage_available=True)
    )
    assert result == statuses


def test_reconcile_group_unavailable_marks_all_gating_unavailable():
    """(b) Group unavailable => every gating source reads "unavailable"."""
    statuses = {
        "service_spend_daily": "ready",
        "warehouse_spend_daily": "ready",
        "database_storage_daily": "ready",
    }
    result = dr._reconcile_completed_source_statuses(
        statuses, _meta(account_usage_available=False)
    )
    assert result == {
        "service_spend_daily": "unavailable",
        "warehouse_spend_daily": "unavailable",
        "database_storage_daily": "unavailable",
    }


def test_reconcile_none_metadata_preserves_statuses():
    """(c) metadata is None => streamed statuses preserved (no crash, no
    force-unavailable on a missing signal)."""
    statuses = {"service_spend_daily": "ready", "warehouse_spend_daily": "ready"}
    result = dr._reconcile_completed_source_statuses(statuses, None)
    assert result == statuses


def test_reconcile_never_alters_non_gating_source():
    """(d) A non-gating source is never altered, even when the group collapsed."""
    statuses = {
        "service_spend_daily": "ready",
        "rate_sheet_daily": "ready",  # non-gating
        "org_spend_daily": "unavailable",  # non-gating
    }
    result = dr._reconcile_completed_source_statuses(
        statuses, _meta(account_usage_available=False)
    )
    # Gating source collapses; non-gating sources pass through untouched.
    assert result["service_spend_daily"] == "unavailable"
    assert result["rate_sheet_daily"] == "ready"
    assert result["org_spend_daily"] == "unavailable"


def test_reconcile_missing_group_field_defaults_available():
    """A metadata dict missing the group field defaults to available (no crash,
    streamed status preserved)."""
    statuses = {"service_spend_daily": "ready"}
    result = dr._reconcile_completed_source_statuses(statuses, {})
    assert result == statuses


def test_reconcile_tolerates_typed_metadata_object():
    """Defensive path: a typed DashboardDatasetMetadata (not a dict) is read via
    getattr and still collapses on an explicit False group flag."""
    settings = Settings()
    metadata = DashboardDatasetMetadata(
        data_mode="estimated",
        account_locator="abc12345",
        currency="USD",
        billing_through_date=None,
        account_usage_through_date=date(2026, 5, 1),
        estimated_credit_price_usd=settings.estimated_credit_price_usd,
        storage_price_usd_per_tb_month=settings.storage_price_usd_per_tb_month,
        organization_usage=SourceAvailability(available=True),
        account_usage=SourceAvailability(available=False),
    )
    result = dr._reconcile_completed_source_statuses(
        {"service_spend_daily": "ready"}, metadata
    )
    assert result == {"service_spend_daily": "unavailable"}

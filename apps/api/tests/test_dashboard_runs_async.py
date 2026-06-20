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
    # Every source that gates a section: the static section deps PLUS both
    # mode-aware overview basis sources (overview is no longer a static entry in
    # SECTION_SOURCE_DEPENDENCIES — its gating is mode-aware via _overview_status).
    return {s for deps in dr.SECTION_SOURCE_DEPENDENCIES.values() for s in deps} | {
        dr.OVERVIEW_BILLED_SOURCE,
        dr.OVERVIEW_ESTIMATED_SOURCE,
    }


def test_section_ready_only_when_all_deps_ready():
    all_ready = {key: "ready" for key in _all_dep_sources()}
    statuses = dr.compute_section_statuses(all_ready)
    assert statuses == {
        "overview": "ready",
        "warehouse": "ready",
        "storage": "ready",
    }


def test_compute_section_statuses_rolls_up_non_ready_cases():
    all_ready = {key: "ready" for key in _all_dep_sources()}
    cases = [
        (
            {"warehouse_spend_daily": "pending"},
            None,
            {"warehouse": "pending"},
        ),
        (
            {**all_ready, "database_storage_daily": "unavailable"},
            None,
            {"storage": "unavailable"},
        ),
        (
            {**all_ready, "service_spend_daily": "failed"},
            "estimated",
            {"overview": "unavailable"},
        ),
        (
            {},
            None,
            {"overview": "pending", "warehouse": "pending", "storage": "pending"},
        ),
    ]

    for source_statuses, data_mode, expected in cases:
        statuses = dr.compute_section_statuses(source_statuses, data_mode=data_mode)
        for section, status in expected.items():
            assert statuses[section] == status


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


def test_running_view_clamps_to_default_30_day_window():
    """Provisional /view for a running run with wide data must return a 30-day
    relative range, not the full source span — previously it returned all ~100
    days until finalize_run snapped it back."""
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)
    # Seed org_spend_daily spanning ~100 days so source bounds are wide and
    # billing_through_date is populated (drives _through_date_for in billed mode).
    dr.dashboard_run_repository.set_dataset(
        run_id,
        "org_spend_daily",
        [
            {
                "usage_date": "2026-03-11",
                "service_type": "WAREHOUSE_METERING",
                "rating_type": "COMPUTE",
                "billing_type": "CONSUMPTION",
                "is_adjustment": False,
                "currency": "USD",
                "spend": 5.0,
            },
            {
                "usage_date": "2026-06-19",
                "service_type": "WAREHOUSE_METERING",
                "rating_type": "COMPUTE",
                "billing_type": "CONSUMPTION",
                "is_adjustment": False,
                "currency": "USD",
                "spend": 8.0,
            },
        ],
    )

    payload = read_dashboard_run_view(run_id, None, None, None, _anon())

    assert payload["run"]["status"] == "running"
    r = payload["range"]
    assert r["mode"] == "relative"
    assert r["window_days"] == 30
    start = date.fromisoformat(r["start_date"])
    end = date.fromisoformat(r["end_date"])
    assert (end - start).days <= 29  # 30-day window = 29-day span


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
    assert final.error == ("An unexpected error occurred while building the dashboard.")
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
    assert final.error == ("Could not query Snowflake billing or Account Usage data.")


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
    assert (
        section["overview"] == "ready"
    ), f"Empty window must stay ready, got {section['overview']!r}"
    assert (
        section["warehouse"] == "ready"
    ), f"Empty window must stay ready, got {section['warehouse']!r}"


def test_completed_snapshot_metadata_none_empty_window_yields_ready():
    """Regression: completed snapshot with metadata=None and all-empty
    account-usage datasets must return all sections "ready", NOT "unavailable".

    This covers the reconstructed-metadata (metadata=None) path specifically.
    When stored metadata is None, get_view_inputs reconstructs it from dataset
    rows with metadata_authoritative=False. The reconstructed metadata for
    all-empty account-usage datasets yields account_usage.available=False —
    which would incorrectly collapse every account-usage gating section to
    "unavailable" if reconciliation ran on it. The fix NO-OPs reconciliation
    for the authoritative=False (reconstructed) case, trusting the seeded
    source states ("ready" for every present dataset key) instead.

    Critically, ALL FOUR ACCOUNT_USAGE_DATASET_KEYS must be empty so the
    reconstructed metadata has account_usage.available=False. Source bounds are
    formed via org_spend_daily (which is NOT in ACCOUNT_USAGE_DATASET_KEYS),
    keeping account_usage_rows empty. The guard is exercised when
    authoritative=True would downgrade overview/warehouse to "unavailable"
    because account_usage.available=False — the authoritative=False NO-OP
    must keep them "ready".
    """
    dr.dashboard_run_repository.clear()
    # All four ACCOUNT_USAGE_DATASET_KEYS are empty-but-present (the regression
    # case). Use org_spend_daily (NOT in ACCOUNT_USAGE_DATASET_KEYS) to anchor
    # source bounds so _source_bounds_for_dataset_rows produces a non-degenerate
    # range matching the requested [2026-05-01, 2026-05-01] window.
    datasets: dict = {key: [] for key in dr.BASE_RUN_SOURCE_KEYS}
    datasets["org_spend_daily"] = [
        {
            "usage_date": "2026-05-01",
            "service_type": "WAREHOUSE_METERING",
            "rating_type": "COMPUTE",
            "billing_type": "CONSUMPTION",
            "is_adjustment": False,
            "currency": "USD",
            "spend": 10.0,
        }
    ]
    run = dr.dashboard_run_repository.create_completed_snapshot(
        organization_id=None,
        source="snowflake",
        window_days=30,
        summary={},
        datasets=datasets,
        metadata=None,  # KEY: triggers reconstruction; metadata_authoritative=False
        retention_days=7,
    )
    run_id = UUID(run.id)

    payload = read_dashboard_run_view(
        run_id, None, date(2026, 5, 1), date(2026, 5, 1), _anon()
    )
    assert payload["run"]["status"] == "completed"
    section = payload["section_statuses"]
    # All three gating sections are present (seeded "ready") despite empty
    # account-usage datasets. The RECONSTRUCTED metadata has
    # account_usage.available=False (no rows across all four
    # ACCOUNT_USAGE_DATASET_KEYS). With authoritative=True this would downgrade
    # overview and warehouse to "unavailable". The authoritative=False NO-OP must
    # preserve the seeded "ready" states.
    assert section["storage"] == "ready", section
    assert (
        section["overview"] == "ready"
    ), f"metadata=None empty window: overview must be ready, got {section['overview']!r}"
    assert (
        section["warehouse"] == "ready"
    ), f"metadata=None empty window: warehouse must be ready, got {section['warehouse']!r}"


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


def test_failed_run_reseeds_base_source_states_unavailable():
    """Finding 2: a run that streamed base source(s) to "ready" then finalizes
    FAILED (metadata=None, datasets={}) must have every base source re-seeded to
    "unavailable" — a stale "ready" must not survive into the /view of a failed
    run with no data.
    """
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)

    # Stream a couple of base sources to "ready" mid-run.
    dr.dashboard_run_repository.set_dataset(
        run_id, "service_spend_daily", [{"usage_date": "2026-05-01"}]
    )
    dr.dashboard_run_repository.complete_source(run_id, "service_spend_daily")
    dr.dashboard_run_repository.set_dataset(
        run_id, "warehouse_spend_daily", [{"usage_date": "2026-05-01"}]
    )
    dr.dashboard_run_repository.complete_source(run_id, "warehouse_spend_daily")
    assert (
        dr.dashboard_run_repository.get_source(run_id, "service_spend_daily").status
        == "ready"
    )

    # Failure finalize: no data at all.
    dr.dashboard_run_repository.finalize_run(
        run_id,
        status="failed",
        summary={},
        metadata=None,
        datasets={},
        error="boom",
    )

    # Every base source must now read "unavailable" (no stale "ready").
    for key in dr.BASE_RUN_SOURCE_KEYS:
        assert (
            dr.dashboard_run_repository.get_source(run_id, key).status == "unavailable"
        ), key

    # And the /view section_statuses reflect reality: nothing is "ready".
    bounds = dr.dashboard_run_repository.get_source_bounds(run_id)
    payload = read_dashboard_run_view(
        run_id, None, bounds.source_start_date, bounds.source_end_date, _anon()
    )
    section = payload["section_statuses"]
    assert section["overview"] != "ready", section
    assert section["warehouse"] != "ready", section
    assert section["storage"] != "ready", section


def test_completed_run_reseeds_present_sources_ready():
    """Finding 2 corollary: re-seeding from final datasets keeps a landed base
    source "ready" on a successful finalize (consistent with streamed state) and
    preserves deferred (non-base) source entries."""
    repo = dr.InMemoryDashboardRunRepository()
    run = repo.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)
    # A deferred (non-base) source state lands before finalize; it must survive.
    repo.complete_source(
        run_id, "ai_consumption_daily", rows=[{"usage_date": "2026-05-02"}]
    )

    repo.finalize_run(
        run_id,
        status="completed",
        summary={},
        metadata=None,
        datasets={"service_spend_daily": [{"usage_date": "2026-05-01"}]},
    )

    # Present base source => "ready"; absent base source => "unavailable".
    assert repo.get_source(run_id, "service_spend_daily").status == "ready"
    assert repo.get_source(run_id, "warehouse_spend_daily").status == "unavailable"
    # Deferred non-base entry preserved through finalize.
    assert repo.get_source_state(run_id, "ai_consumption_daily") == "completed"


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


def test_reconcile_never_alters_non_gating_source():
    """(d) A non-gating source is never altered, even when the group collapsed.

    org_spend_daily IS a gating source now (group "organization_usage"), but
    _meta leaves that group available, so its input "unavailable" passes through
    unchanged. rate_sheet_daily is non-gating and never touched.
    """
    statuses = {
        "service_spend_daily": "ready",  # gating, account_usage (collapsed)
        "rate_sheet_daily": "ready",  # non-gating
        "org_spend_daily": "unavailable",  # gating, organization_usage (available)
    }
    result = dr._reconcile_completed_source_statuses(
        statuses, _meta(account_usage_available=False)
    )
    # account_usage gating source collapses; org group available + non-gating
    # source pass through untouched.
    assert result["service_spend_daily"] == "unavailable"
    assert result["rate_sheet_daily"] == "ready"
    assert result["org_spend_daily"] == "unavailable"


# --- Finding 1: mode-aware overview gating (billed vs estimated) --------------


def _org_spend_rows() -> list[dict]:
    return [
        {
            "usage_date": "2026-05-01",
            "service_type": "WAREHOUSE_METERING",
            "rating_type": "COMPUTE",
            "billing_type": "CONSUMPTION",
            "is_adjustment": False,
            "currency": "USD",
            "spend": 10.0,
        }
    ]


def _billed_metadata(
    *, account_usage_available: bool, organization_usage_available: bool = True
) -> dict:
    """Completed-snapshot metadata for a BILLED run: data_mode="billed" so the
    completed /view branch gates overview on org_spend_daily (Organization
    Usage), not service_spend_daily (Account Usage)."""
    settings = Settings()
    return DashboardDatasetMetadata(
        data_mode="billed",
        account_locator="abc12345",
        currency="USD",
        billing_through_date=date(2026, 5, 1),
        account_usage_through_date=date(2026, 5, 1),
        estimated_credit_price_usd=settings.estimated_credit_price_usd,
        storage_price_usd_per_tb_month=settings.storage_price_usd_per_tb_month,
        organization_usage=SourceAvailability(available=organization_usage_available),
        account_usage=SourceAvailability(available=account_usage_available),
    ).model_dump(mode="json")


def test_completed_billed_overview_ready_when_account_usage_collapsed():
    """Finding 1 (completed/billed): Account Usage collapsed (service_spend_daily
    unavailable) but Organization Usage available + org_spend_daily ready =>
    overview is "ready" — the billed overview renders from org_spend_daily."""
    dr.dashboard_run_repository.clear()
    datasets: dict = {key: [] for key in dr.BASE_RUN_SOURCE_KEYS}
    datasets["org_spend_daily"] = _org_spend_rows()
    run = dr.dashboard_run_repository.create_completed_snapshot(
        organization_id=None,
        source="snowflake",
        window_days=30,
        summary={},
        datasets=datasets,
        metadata=_billed_metadata(
            account_usage_available=False, organization_usage_available=True
        ),
        retention_days=7,
    )
    run_id = UUID(run.id)
    payload = read_dashboard_run_view(
        run_id, None, date(2026, 5, 1), date(2026, 5, 1), _anon()
    )
    section = payload["section_statuses"]
    assert section["overview"] == "ready", section
    # warehouse/storage genuinely read Account Usage -> still unavailable.
    assert section["warehouse"] == "unavailable", section
    assert section["storage"] == "unavailable", section


def test_completed_estimated_overview_ready_when_account_usage_available():
    """Finding 1 (completed/estimated): account_usage available + service_spend
    ready => overview "ready"."""
    dr.dashboard_run_repository.clear()
    datasets: dict = {key: [] for key in dr.BASE_RUN_SOURCE_KEYS}
    run = dr.dashboard_run_repository.create_completed_snapshot(
        organization_id=None,
        source="snowflake",
        window_days=30,
        summary={},
        datasets=datasets,
        metadata=_completed_metadata(account_usage_available=True),  # estimated
        retention_days=7,
    )
    run_id = UUID(run.id)
    bounds = dr.dashboard_run_repository.get_source_bounds(run_id)
    payload = read_dashboard_run_view(
        run_id, None, bounds.source_start_date, bounds.source_end_date, _anon()
    )
    assert payload["section_statuses"]["overview"] == "ready", payload[
        "section_statuses"
    ]


def test_completed_estimated_overview_unavailable_when_account_usage_collapsed():
    """Finding 1 (completed/estimated, unchanged): account_usage collapsed =>
    overview "unavailable" — estimated overview gates on service_spend_daily."""
    dr.dashboard_run_repository.clear()
    datasets: dict = {key: [] for key in dr.BASE_RUN_SOURCE_KEYS}
    run = dr.dashboard_run_repository.create_completed_snapshot(
        organization_id=None,
        source="snowflake",
        window_days=30,
        summary={},
        datasets=datasets,
        metadata=_completed_metadata(account_usage_available=False),  # estimated
        retention_days=7,
    )
    run_id = UUID(run.id)
    bounds = dr.dashboard_run_repository.get_source_bounds(run_id)
    payload = read_dashboard_run_view(
        run_id, None, bounds.source_start_date, bounds.source_end_date, _anon()
    )
    assert payload["section_statuses"]["overview"] == "unavailable", payload[
        "section_statuses"
    ]


def test_streaming_overview_ready_when_only_org_spend_landed():
    """Finding 1 (streaming): org_spend_daily landed but service_spend_daily has
    NOT => overview "ready" (was "pending" before — the billed basis is
    renderable). data_mode is unknown while running, so OR semantics apply."""
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)
    dr.dashboard_run_repository.set_dataset(
        run_id, "org_spend_daily", _org_spend_rows()
    )
    dr.dashboard_run_repository.complete_source(run_id, "org_spend_daily")

    payload = read_dashboard_run_view(run_id, None, None, None, _anon())
    assert payload["run"]["status"] == "running"
    section = payload["section_statuses"]
    assert section["overview"] == "ready", section
    # service_spend has not landed but the billed basis is enough.
    assert section["warehouse"] == "pending", section


def test_streaming_overview_ready_when_only_service_spend_landed():
    """Finding 1 (streaming): service_spend_daily landed but org_spend_daily has
    NOT => overview "ready" (the estimated basis is renderable)."""
    dr.dashboard_run_repository.clear()
    run = dr.dashboard_run_repository.create_running_run(
        organization_id=None,
        source="snowflake",
        window_days=100,
        expected_sources=dr.BASE_RUN_SOURCE_KEYS,
        retention_days=7,
    )
    run_id = UUID(run.id)
    dr.dashboard_run_repository.set_dataset(
        run_id,
        "service_spend_daily",
        [
            {
                "usage_date": "2026-05-01",
                "service_type": "WAREHOUSE_METERING",
                "credits_used": 2.0,
            }
        ],
    )
    dr.dashboard_run_repository.complete_source(run_id, "service_spend_daily")

    payload = read_dashboard_run_view(run_id, None, None, None, _anon())
    assert payload["run"]["status"] == "running"
    assert payload["section_statuses"]["overview"] == "ready", payload[
        "section_statuses"
    ]


def test_streaming_overview_unavailable_only_when_both_bases_fail():
    """Finding 1 (streaming): overview is "unavailable" only when BOTH bases are
    unavailable/failed; one failed + one pending stays "pending"."""
    base_failed_other_pending = {
        dr.OVERVIEW_ESTIMATED_SOURCE: "failed",
        # org_spend_daily not present => pending
    }
    assert (
        dr.compute_section_statuses(base_failed_other_pending)["overview"] == "pending"
    )
    both_failed = {
        dr.OVERVIEW_ESTIMATED_SOURCE: "failed",
        dr.OVERVIEW_BILLED_SOURCE: "unavailable",
    }
    assert dr.compute_section_statuses(both_failed)["overview"] == "unavailable"

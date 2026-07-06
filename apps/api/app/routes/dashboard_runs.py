import logging
from datetime import date, datetime, timedelta, timezone
from threading import RLock, Thread
from typing import Any, NamedTuple
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.auth import AuthContext, require_auth_context, require_org_membership
from app.config import Settings
from app.models import (
    DashboardDatasetMetadata,
    DashboardDatasetResponse,
    DashboardRun,
    DashboardRunCreateRequest,
    SourceAvailability,
)
from app.services.audit_events import audit_event_recorder
from app.services.dashboard_datasets import (
    FETCH_WINDOW_DAYS,
    DashboardSourcesUnavailableError,
    build_snowflake_dashboard_data,
)
from app.services.dashboard_view_builder import (
    DEFAULT_VIEW_WINDOW_DAYS,
    DashboardInvalidRangeError,
    DashboardRangeOutOfBoundsError,
    SUPPORTED_VIEW_WINDOW_DAYS,
    _through_date_for,
    build_ai_detail_view,
    build_dashboard_view,
    resolve_dashboard_view_range,
    window_start_for,
)
from app.services.dashboard_cache_settings import (
    get_cache_settings_store,
    read_cache_settings,
)
from app.services.dashboard_run_cache import (
    CachedDashboardRun,
    RunCacheStoreError,
    get_run_cache_store,
)
from app.services.dashboard_view_models import DashboardViewRange, DashboardViewResponse
from app.services.deferred_sources import DEFERRED_SOURCES
from app.services.demo_data import build_demo_dashboard_dataset
from app.services.parallel_source_runner import SourceOutcome

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard-runs", tags=["dashboard-runs"])
ACCOUNT_USAGE_DATASET_KEYS = (
    "warehouse_spend_daily",
    "service_spend_daily",
    "query_compute_by_user_daily",
    "database_storage_daily",
)

# Base sources whose readiness gates the progressive view. AI stays deferred
# (its own /sources poll); current_account, account_spend_daily, and
# top_warehouses_table are synthesized/derived at finalize, not streamed.
BASE_RUN_SOURCE_KEYS: tuple[str, ...] = (
    "warehouse_spend_daily",
    "service_spend_daily",
    "query_compute_by_user_daily",
    "database_storage_daily",
    "org_spend_daily",
    "rate_sheet_daily",
    "capacity_balance_daily",
)

# Source->section gating matrix. Each section renders as soon as its PRIMARY
# source(s) land. Secondary inputs the view builder already tolerates when empty
# are intentionally EXCLUDED so their lag never blocks a section:
#   - capacity_balance_daily        (overview capacity strip)
#   - query_compute_by_user_daily   (per-user warehouse breakdown)
#   - rate_sheet_daily              (currency conversion)
# rate_sheet_daily is OMITTED here on purpose: build_dashboard_view degrades
# gracefully on an empty rate sheet (native/USD pricing, no crash — verified in
# _build_rate_index / _estimated_conversion_unsupported_view_model). The
# trade-off (premature-readiness risk): a converted-currency section may flip to
# "ready" and render in native/stale FX a beat before the rate sheet lands.
#   - overview  -> total_spend + service breakdown => MODE-AWARE (see below)
#   - warehouse -> warehouse_spend                 => warehouse_spend_daily
#   - storage   -> storage_spend                    => database_storage_daily
#
# OVERVIEW is mode-aware and so is NOT a static single-source entry here. In
# BILLED (or demo) mode the overview's total-spend (_daily_billed_totals) and
# service breakdown render from org_spend_daily (Organization Usage); in
# ESTIMATED mode they render from service_spend_daily (Account Usage). Because
# the basis is only known once data_mode is finalized, overview readiness is
# computed by _overview_status (mode-aware) rather than this static matrix.
# warehouse and storage genuinely read their Account Usage sources in BOTH modes,
# so they keep their static single-source AND gating.
SECTION_SOURCE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "warehouse": ("warehouse_spend_daily",),
    "storage": ("database_storage_daily",),
}

# The two sources that can render the overview, one per data_mode. Either basis
# is independently renderable: whichever lands first makes overview "ready".
OVERVIEW_BILLED_SOURCE = "org_spend_daily"  # Organization Usage (billed/demo)
OVERVIEW_ESTIMATED_SOURCE = "service_spend_daily"  # Account Usage (estimated)

# Maps each gating source to the finalized metadata availability field that
# records whether that source's GROUP collapsed at finalize. _group_from_outcomes
# collapses an entire source group together and persists the group's availability
# in the view metadata under "account_usage" / "organization_usage".
# snowflake_account_usage sources map to "account_usage"; org_spend_daily is kind
# snowflake_organization_usage, so it maps to "organization_usage".
#
# IMPORTANT: this MUST be extended whenever a NEW gating source (a static
# SECTION_SOURCE_DEPENDENCIES value OR an overview basis source) of a different
# kind is added. The assertion below fails fast if a gating source is ever added
# without a corresponding group entry.
GATING_SOURCE_GROUP: dict[str, str] = {
    "service_spend_daily": "account_usage",
    "warehouse_spend_daily": "account_usage",
    "database_storage_daily": "account_usage",
    "org_spend_daily": "organization_usage",
}

# Fail-fast guard: every gating source — the static section deps AND both
# mode-aware overview basis sources — MUST have an availability-group entry, or
# its collapse signal could never be read. Keeps GATING_SOURCE_GROUP in lockstep
# with the gating set.
assert (
    {dep for deps in SECTION_SOURCE_DEPENDENCIES.values() for dep in deps}
    | {OVERVIEW_BILLED_SOURCE, OVERVIEW_ESTIMATED_SOURCE}
) <= set(GATING_SOURCE_GROUP), (
    "Every gating source (static section deps + overview basis sources) must "
    "have a GATING_SOURCE_GROUP availability-field entry."
)


def _roll_up_dep_states(dep_states: list[str]) -> str:
    """ready iff every dep is ready; unavailable if any is unavailable/failed;
    pending otherwise."""
    if any(state in {"unavailable", "failed"} for state in dep_states):
        return "unavailable"
    if all(state == "ready" for state in dep_states):
        return "ready"
    return "pending"


def _overview_status(source_statuses: dict[str, str], *, data_mode: str | None) -> str:
    """Mode-aware overview readiness.

    The overview renders from org_spend_daily (Organization Usage) in BILLED/demo
    mode and from service_spend_daily (Account Usage) in ESTIMATED mode — either
    basis is independently renderable.

    - ``data_mode is None`` (streaming / not yet finalized): the basis is unknown,
      so overview is "ready" when EITHER basis source is ready (whichever lands
      first is renderable), "unavailable" only when BOTH are unavailable/failed,
      else "pending". This unblocks a billed overview that is still waiting on (or
      has lost) Account Usage.
    - ``data_mode`` known ("billed"/"demo"/"estimated"): the basis source is
      selected by mode and the overview rolls up that single source's state.
    """
    billed_state = source_statuses.get(OVERVIEW_BILLED_SOURCE, "pending")
    estimated_state = source_statuses.get(OVERVIEW_ESTIMATED_SOURCE, "pending")
    if data_mode in {"billed", "demo"}:
        return _roll_up_dep_states([billed_state])
    if data_mode == "estimated":
        return _roll_up_dep_states([estimated_state])
    # Streaming / unknown mode: OR semantics across the two possible bases.
    if "ready" in {billed_state, estimated_state}:
        return "ready"
    if billed_state in {"unavailable", "failed"} and estimated_state in {
        "unavailable",
        "failed",
    }:
        return "unavailable"
    return "pending"


def compute_section_statuses(
    source_statuses: dict[str, str], *, data_mode: str | None = None
) -> dict[str, str]:
    """Roll per-source readiness up into per-section status.

    ready       — every dependency is "ready"
    unavailable — at least one dependency is "unavailable" or "failed"
    pending     — otherwise (a dependency is still pending/unknown)

    ``data_mode`` selects the overview's gating basis (see _overview_status). It
    is None on the streaming path (the basis is unknown until finalize) and the
    finalized data_mode on the completed/authoritative path. warehouse and
    storage are mode-independent and always use their static single-source gating.
    """
    result: dict[str, str] = {
        "overview": _overview_status(source_statuses, data_mode=data_mode),
    }
    for section, deps in SECTION_SOURCE_DEPENDENCIES.items():
        dep_states = [source_statuses.get(dep, "pending") for dep in deps]
        result[section] = _roll_up_dep_states(dep_states)
    return result


# Wall-clock ceiling for a run stuck in "running"; independent of dataset
# retention. A worker that dies without finalizing can never leave a run
# permanently running.
RUNNING_RUN_TTL_SECONDS = 300

# Run statuses past which a run is settled. A base source landing late (after
# finalize/expire/delete) must be a silent no-op for these.
_TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "expired", "deleted"}
)


class StoredDashboardDataset(BaseModel):
    aggregate_dataset: list[dict[str, Any]]
    retention_expires_at: datetime


class StoredSourceBounds(BaseModel):
    source_start_date: date
    source_end_date: date


class SourceRecord(NamedTuple):
    """Lightweight read view of a source state entry."""

    status: str
    meta: dict[str, Any]


class InMemoryDashboardRunRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._runs: dict[UUID, DashboardRun] = {}
        self._summaries: dict[UUID, dict[str, Any]] = {}
        self._datasets: dict[UUID, dict[str, StoredDashboardDataset]] = {}
        self._metadata: dict[UUID, dict[str, Any] | None] = {}
        self._source_bounds: dict[UUID, StoredSourceBounds] = {}
        self._source_states: dict[UUID, dict[str, dict[str, Any]]] = {}
        self._retention_days: dict[UUID, int] = {}
        self._running_deadlines: dict[UUID, datetime] = {}

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._summaries.clear()
            self._datasets.clear()
            self._metadata.clear()
            self._source_bounds.clear()
            self._source_states.clear()
            self._retention_days.clear()
            self._running_deadlines.clear()

    def create_completed_run(self, request: DashboardRunCreateRequest) -> DashboardRun:
        return self.create_completed_snapshot(
            organization_id=request.organization_id,
            source=request.source,
            window_days=request.window_days,
            summary=request.summary,
            datasets=request.datasets,
            metadata=None,
            retention_days=request.retention_days,
        )

    def create_completed_snapshot(
        self,
        *,
        organization_id: UUID | None,
        source: str,
        window_days: int,
        summary: dict[str, Any],
        datasets: dict[str, list[dict[str, Any]]],
        metadata: dict[str, Any] | None = None,
        retention_days: int,
    ) -> DashboardRun:
        now = datetime.now(timezone.utc)
        run_id = uuid4()
        run = DashboardRun(
            id=str(run_id),
            organization_id=organization_id,
            source=source,
            status="completed",
            window_days=window_days,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        retention_expires_at = now + timedelta(days=retention_days)
        # Seed source states for every base key so completed-view section_statuses
        # reflect final readiness rather than defaulting to "idle" → "pending".
        # A key present in datasets is "ready"; an absent key is "unavailable".
        # NOTE: an empty [] dataset is ambiguous — it can mean either "no rows in
        # the window" OR a collapsed/unavailable source group (see
        # _group_from_outcomes). The /view completed branch reconciles "ready"
        # against finalized GROUP availability (metadata.account_usage /
        # organization_usage), NOT dataset emptiness, so only a genuinely
        # collapsed group surfaces as "unavailable" — an empty window stays ready.
        seeded_source_states = {
            key: {"status": "ready" if key in datasets else "unavailable"}
            for key in BASE_RUN_SOURCE_KEYS
        }
        # Deferred sources (e.g. AI consumption) are fetched AFTER the main run
        # and are absent from BASE_RUN_SOURCE_KEYS. When a snapshot is loaded from
        # the durable cache and its datasets already carry a deferred source, seed
        # that source as "completed" so read_dashboard_source serves it straight
        # from the stored rows instead of claim_source succeeding and re-running a
        # live Snowflake fetch. Deferred keys ABSENT from datasets are left unseeded
        # so a snapshot without AI data can still be triggered on demand.
        for key in DEFERRED_SOURCES:
            if key in datasets:
                seeded_source_states[key] = {"status": "completed"}
        with self._lock:
            self._runs[run_id] = run
            self._summaries[run_id] = summary
            self._metadata[run_id] = metadata
            self._datasets[run_id] = {
                dataset_key: StoredDashboardDataset(
                    aggregate_dataset=rows,
                    retention_expires_at=retention_expires_at,
                )
                for dataset_key, rows in datasets.items()
            }
            self._retention_days[run_id] = retention_days
            self._source_states[run_id] = seeded_source_states
            self._store_source_bounds(run_id, datasets)
        return run

    def get_run(self, run_id: UUID) -> DashboardRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None and run.status == "running":
                # Lazily apply wall-clock TTL expiry on read.
                self._is_running_locked(run_id)
                run = self._runs.get(run_id)
            return run

    def get_source_bounds(self, run_id: UUID) -> StoredSourceBounds | None:
        with self._lock:
            return self._source_bounds.get(run_id)

    def get_view_inputs(
        self, run_id: UUID
    ) -> (
        tuple[
            DashboardRun,
            dict[str, list[dict[str, Any]]],
            DashboardDatasetMetadata,
            StoredSourceBounds,
            dict[str, str] | None,
            bool,
        ]
        | None
    ):
        """Atomic view snapshot: datasets, metadata, bounds AND the base
        source-status map are all read under a SINGLE lock acquisition so a
        concurrent worker write can never split a "ready" status from a stale
        empty dataset. ``source_statuses`` is None for non-snowflake runs.

        The final tuple element ``metadata_authoritative`` is True iff the
        returned metadata came from stored (finalized) metadata rather than
        being RECONSTRUCTED from dataset rows. Group-availability reconciliation
        (``_reconcile_completed_source_statuses``) must only downgrade gating
        sources when metadata is authoritative; reconstructed metadata has no
        real collapse signal, so the streamed/seeded statuses are trusted as-is.
        """
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.status == "deleted":
                return None

            datasets = self._datasets.get(run_id)
            if datasets is None:
                return None

            if self._is_run_expired(datasets):
                self._expire_run_locked(run_id, run)
                return None

            metadata = self._metadata.get(run_id)
            source_bounds = self._source_bounds.get(run_id)
            if source_bounds is None:
                return None
            dataset_rows = {
                dataset_key: stored_dataset.aggregate_dataset
                for dataset_key, stored_dataset in datasets.items()
            }
            states = self._source_states.get(run_id, {})
            source_statuses = (
                {
                    key: states.get(key, {}).get("status", "idle")
                    for key in BASE_RUN_SOURCE_KEYS
                }
                if run.source == "snowflake"
                else None
            )
            metadata_authoritative = metadata is not None
            return (
                run,
                dataset_rows,
                DashboardDatasetMetadata.model_validate(metadata)
                if metadata is not None
                else _metadata_for_dataset_rows(dataset_rows),
                source_bounds,
                source_statuses,
                metadata_authoritative,
            )

    def _store_source_bounds(
        self,
        run_id: UUID,
        datasets: dict[str, list[dict[str, Any]]],
    ) -> None:
        self._source_bounds[run_id] = _source_bounds_for_dataset_rows(datasets)

    @staticmethod
    def _is_run_expired(datasets: dict[str, StoredDashboardDataset]) -> bool:
        return any(
            dataset_is_expired(dataset.retention_expires_at)
            for dataset in list(datasets.values())
        )

    def _expire_run_locked(self, run_id: UUID, run: DashboardRun) -> None:
        """Mark a run expired and drop all of its now-invalid in-memory state.

        Callers must already hold ``self._lock``. Source state is cleared here so
        it can never outlive the data it describes.
        """
        self._runs[run_id] = run.model_copy(
            update={
                "status": "expired",
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self._datasets.pop(run_id, None)
        self._metadata.pop(run_id, None)
        self._source_bounds.pop(run_id, None)
        self._source_states.pop(run_id, None)
        self._retention_days.pop(run_id, None)
        self._running_deadlines.pop(run_id, None)

    def get_dataset_response(self, run_id: UUID) -> DashboardDatasetResponse | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.status == "deleted":
                return None

            stored_datasets = self._datasets.get(run_id)
            if stored_datasets is None:
                return None
            if self._is_run_expired(stored_datasets):
                self._expire_run_locked(run_id, run)
                return None

            stored_metadata = self._metadata.get(run_id)
            return DashboardDatasetResponse(
                run=run,
                summary=self._summaries.get(run_id, {}),
                datasets={
                    dataset_key: stored_dataset.aggregate_dataset
                    for dataset_key, stored_dataset in stored_datasets.items()
                },
                metadata=(
                    DashboardDatasetMetadata.model_validate(stored_metadata)
                    if stored_metadata is not None
                    else None
                ),
            )

    def expire_run_datasets(self, run_id: UUID) -> None:
        with self._lock:
            stored_datasets = self._datasets.get(run_id, {})
            expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            self._datasets[run_id] = {
                dataset_key: stored_dataset.model_copy(
                    update={"retention_expires_at": expired_at}
                )
                for dataset_key, stored_dataset in stored_datasets.items()
            }

    def delete_run(self, run_id: UUID) -> DashboardRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None

            deleted_run = run.model_copy(
                update={"status": "deleted", "updated_at": datetime.now(timezone.utc)}
            )
            self._runs[run_id] = deleted_run
            self._datasets.pop(run_id, None)
            self._metadata.pop(run_id, None)
            self._source_bounds.pop(run_id, None)
            self._source_states.pop(run_id, None)
            self._retention_days.pop(run_id, None)
            self._running_deadlines.pop(run_id, None)
            return deleted_run

    def claim_source(self, run_id: UUID, source_id: str) -> bool:
        """Mark a source in-flight. Returns False if the run is not completed or
        running, is deleted/expired, or a fetch is already pending."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return False
            if run.status == "running":
                # Running run: use the wall-clock TTL guard instead of dataset expiry.
                if not self._is_running_locked(run_id):
                    return False
            elif run.status == "completed":
                datasets = self._datasets.get(run_id)
                if datasets is None:
                    return False
                # Expiry is lazy: a still-"completed" run whose datasets have aged out
                # must not be claimed (it would trigger wasted Snowflake work and
                # leave the source pending). Apply the same transition get_view_inputs
                # uses, clearing the now-invalid in-memory state including source state.
                if self._is_run_expired(datasets):
                    self._expire_run_locked(run_id, run)
                    return False
            else:
                return False
            states = self._source_states.setdefault(run_id, {})
            current = states.get(source_id, {}).get("status")
            if current in {"pending", "completed"}:
                return False
            states[source_id] = {"status": "pending"}
            return True

    def get_source_state(self, run_id: UUID, source_id: str) -> str | None:
        with self._lock:
            return self._source_states.get(run_id, {}).get(source_id, {}).get("status")

    def get_source_meta(self, run_id: UUID, source_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._source_states.get(run_id, {}).get(source_id)
            return dict(state) if state is not None else None

    def get_source(self, run_id: UUID, source_id: str) -> SourceRecord:
        """Return a SourceRecord for a source. Status is 'idle' if not yet set."""
        with self._lock:
            state = self._source_states.get(run_id, {}).get(source_id, {})
            return SourceRecord(
                status=state.get("status", "idle"),
                meta={k: v for k, v in state.items() if k != "status"},
            )

    def complete_source(
        self,
        run_id: UUID,
        source_id: str,
        *,
        rows: list[dict[str, Any]] | None = None,
        partial: bool = False,
        skipped_branches: list[str] | None = None,
    ) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            # A slow BASE source landing after the run is already terminal must
            # never mutate or resurrect the run. The deferred-AI path
            # (non-base sources) legitimately updates post-completion, so it is
            # not guarded here.
            if (
                source_id in BASE_RUN_SOURCE_KEYS
                and run.status in _TERMINAL_RUN_STATUSES
            ):
                return
            if run.status == "running":
                # Staleness guard for running runs.
                if not self._is_running_locked(run_id):
                    return
            stored = self._datasets.get(run_id)
            if stored is None:
                return
            # Only write dataset rows when provided (deferred AI sources always
            # provide rows; base sources may call complete_source after set_dataset).
            if rows is not None:
                # Inherit the run's existing retention so the deferred dataset can
                # never outlive — or prematurely expire — the run.
                retention = next(
                    (d.retention_expires_at for d in stored.values()),
                    datetime.now(timezone.utc) + timedelta(days=7),
                )
                stored[source_id] = StoredDashboardDataset(
                    aggregate_dataset=rows, retention_expires_at=retention
                )
            # "ready" (streamed, not yet terminal) applies ONLY to BASE sources
            # while the base run is still running. A DEFERRED (non-base) source
            # finishing during a running base run must stamp "completed" so its
            # GET /sources/{id} poll can reach a terminal/served state instead of
            # degrading to error.
            is_streaming_base = (
                source_id in BASE_RUN_SOURCE_KEYS and run.status == "running"
            )
            self._source_states.setdefault(run_id, {})[source_id] = {
                "status": "ready" if is_streaming_base else "completed",
                "partial": partial,
                "skipped_branches": list(skipped_branches or []),
            }

    def fail_source(
        self, run_id: UUID, source_id: str, *, error: str | None = None
    ) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            # Same terminal-state guard as complete_source: a late BASE source
            # failure must not mutate an already-terminal run.
            if (
                run is not None
                and source_id in BASE_RUN_SOURCE_KEYS
                and run.status in _TERMINAL_RUN_STATUSES
            ):
                return
            if run is not None and run.status == "running":
                if not self._is_running_locked(run_id):
                    return
            # "unavailable" is reserved for a BASE source whose readiness gates the
            # progressive view while the base run is still running. A DEFERRED
            # (non-base) source — fetched via /sources/{id} — must fail terminally
            # as "failed": the frontend's source poll treats only
            # completed/failed/expired as terminal, so "unavailable" would leave it
            # polling until timeout and then surfacing a generic error.
            is_running_base = (
                run is not None
                and run.status == "running"
                and source_id in BASE_RUN_SOURCE_KEYS
            )
            state: dict[str, Any] = {
                "status": "unavailable" if is_running_base else "failed"
            }
            if error is not None:
                state["error"] = error
            self._source_states.setdefault(run_id, {})[source_id] = state

    def create_running_run(
        self,
        *,
        organization_id: UUID | None,
        source: str,
        window_days: int,
        expected_sources: tuple[str, ...],
        retention_days: int,
    ) -> DashboardRun:
        now = datetime.now(timezone.utc)
        run_id = uuid4()
        run = DashboardRun(
            id=str(run_id),
            organization_id=organization_id,
            source=source,
            status="running",
            window_days=window_days,
            started_at=now,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._runs[run_id] = run
            self._summaries[run_id] = {}
            self._metadata[run_id] = None
            self._datasets[run_id] = {}
            self._source_bounds[run_id] = _source_bounds_for_dataset_rows({})
            self._source_states[run_id] = {
                key: {"status": "pending"} for key in expected_sources
            }
            self._retention_days[run_id] = retention_days
            self._running_deadlines[run_id] = now + timedelta(
                seconds=RUNNING_RUN_TTL_SECONDS
            )
        return run

    def _is_running_locked(self, run_id: UUID) -> bool:
        """Staleness guard: True only if the run is still actively running.

        Callers must already hold ``self._lock``.
        """
        run = self._runs.get(run_id)
        if run is None or run.status != "running":
            return False
        deadline = self._running_deadlines.get(run_id)
        if deadline is not None and deadline <= datetime.now(timezone.utc):
            self._expire_run_locked(run_id, run)
            return False
        return True

    def set_dataset(self, run_id: UUID, key: str, rows: list[dict[str, Any]]) -> None:
        with self._lock:
            if not self._is_running_locked(run_id):
                return
            retention_days = self._retention_days.get(run_id, 7)
            expires_at = datetime.now(timezone.utc) + timedelta(days=retention_days)
            stored = self._datasets.setdefault(run_id, {})
            stored[key] = StoredDashboardDataset(
                aggregate_dataset=rows, retention_expires_at=expires_at
            )
            # Recompute provisional bounds from everything landed so far.
            self._store_source_bounds(
                run_id,
                {k: d.aggregate_dataset for k, d in stored.items()},
            )

    def finalize_run(
        self,
        run_id: UUID,
        *,
        status: str,
        summary: dict[str, Any],
        metadata: dict[str, Any] | None,
        datasets: dict[str, list[dict[str, Any]]],
        error: str | None = None,
    ) -> None:
        with self._lock:
            # Only finalize a run that is still running; never resurrect a
            # deleted/expired run or re-finalize a completed one.
            if not self._is_running_locked(run_id):
                return
            run = self._runs[run_id]
            now = datetime.now(timezone.utc)
            retention_days = self._retention_days.get(run_id, 7)
            expires_at = now + timedelta(days=retention_days)

            # Build/validate EVERYTHING that can raise into LOCAL variables FIRST,
            # before mutating any run state. If StoredDashboardDataset validation
            # or source-bounds computation raises here, the run is still
            # "running", so the worker's fallback finalize_run(status="failed")
            # is NOT no-opped by the _is_running_locked guard above — the run can
            # still reach a terminal state instead of being stranded
            # half-finalized.
            # Preserve any NON-BASE stored datasets (e.g. an AI deferred source
            # that completed while the base run was still running) — finalize only
            # carries the base run's datasets, so a naive replace would wipe a
            # deferred dataset whose source state is (correctly) "completed",
            # leaving its /sources poll serving an empty view. Base keys are
            # rebuilt from the final datasets below. Built immutably.
            #
            # Re-wrap preserved deferred datasets with the FINAL run expiry rather
            # than their original retention. A deferred source that completed
            # before any base dataset had landed got complete_source's hard-coded
            # 7-day fallback expiry; keeping it would expire the whole run after 7
            # days (expiry checks fail on ANY expired dataset) even when the run
            # requested a longer retention. All datasets in a finalized run now
            # share the same expiry, matching the base datasets rebuilt below.
            existing_datasets = self._datasets.get(run_id, {})
            preserved_deferred = {
                key: StoredDashboardDataset(
                    aggregate_dataset=dataset.aggregate_dataset,
                    retention_expires_at=expires_at,
                )
                for key, dataset in existing_datasets.items()
                if key not in BASE_RUN_SOURCE_KEYS and key not in datasets
            }
            stored_datasets = {
                **preserved_deferred,
                **{
                    key: StoredDashboardDataset(
                        aggregate_dataset=rows, retention_expires_at=expires_at
                    )
                    for key, rows in datasets.items()
                },
            }
            # Bounds derive from the FINAL base datasets only (the deferred AI
            # source spans the same fetch window and uses the run's own bounds via
            # _through_date_for at read time), preserving existing behavior.
            source_bounds = _source_bounds_for_dataset_rows(datasets)
            # Re-seed base source states from the FINAL datasets so per-source
            # states reflect reality. A base key present in the final datasets is
            # "ready"; an absent key is "unavailable". For a failed run
            # (datasets={}) every base source becomes "unavailable", overwriting
            # any stale streamed "ready"/"unavailable" state. Non-base/deferred
            # entries (e.g. AI deferred sources) are PRESERVED. Built as an
            # immutable rebuild rather than mutating the nested dicts in place.
            existing_states = self._source_states.get(run_id, {})
            reseeded_states = {
                key: state
                for key, state in existing_states.items()
                if key not in BASE_RUN_SOURCE_KEYS
            }
            for key in BASE_RUN_SOURCE_KEYS:
                reseeded_states[key] = {
                    "status": "ready" if key in datasets else "unavailable"
                }

            # All fallible work is done; now mutate state atomically.
            self._runs[run_id] = run.model_copy(
                update={
                    "status": status,
                    "completed_at": now,
                    "updated_at": now,
                    "error": error,
                }
            )
            self._running_deadlines.pop(run_id, None)
            self._summaries[run_id] = summary
            self._metadata[run_id] = metadata
            self._datasets[run_id] = stored_datasets
            self._source_bounds[run_id] = source_bounds
            self._source_states[run_id] = reseeded_states


dashboard_run_repository = InMemoryDashboardRunRepository()


def dataset_is_expired(expires_at: datetime, *, now: datetime | None = None) -> bool:
    comparison_time = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= comparison_time


def _require_dashboard_run_membership(
    auth_context: AuthContext, organization_id: UUID
) -> None:
    if auth_context.auth_required:
        require_org_membership(auth_context, str(organization_id))


@router.get("/demo", response_model=DashboardRun)
def read_demo_dashboard_run() -> DashboardRun:
    demo_run = build_demo_dashboard_dataset().run
    return DashboardRun.model_validate(demo_run.model_dump(mode="json"))


@router.get("/demo/datasets")
def read_demo_dashboard_datasets() -> dict[str, Any]:
    payload = build_demo_dashboard_dataset()
    return payload.model_dump(mode="json")


@router.get("/demo/view", response_model=DashboardViewResponse)
def read_demo_dashboard_view(
    window_days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    payload = build_demo_dashboard_dataset()
    run = DashboardRun.model_validate(payload.run.model_dump(mode="json"))
    bounds = _source_bounds_for_dataset_rows(payload.datasets)
    view = _prepared_view_or_http_error(
        run=run,
        datasets=payload.datasets,
        metadata=payload.metadata,
        source_bounds=bounds,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )
    return view.model_dump(mode="json")


@router.get("/demo/sources/{source_id}")
def read_demo_dashboard_source(
    source_id: str,
    window_days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    if source_id not in DEFERRED_SOURCES:
        raise HTTPException(status_code=404, detail="Unknown deferred source")
    payload = build_demo_dashboard_dataset()
    bounds = _source_bounds_for_dataset_rows(payload.datasets)
    through_date = payload.metadata.account_usage_through_date or bounds.source_end_date
    view_range = _resolve_source_view_range_or_http_error(
        through_date=through_date,
        source_start_date=bounds.source_start_date,
        source_end_date=bounds.source_end_date,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )
    view = build_ai_detail_view(
        ai_rows=payload.datasets.get(source_id, []),
        rate_rows=payload.datasets.get("rate_sheet_daily", []),
        currency=payload.metadata.currency or "USD",
        estimated_credit_price_usd=payload.metadata.estimated_credit_price_usd,
        start_date=view_range.start_date,
        end_date=view_range.end_date,
        partial=False,
        skipped_branches=[],
    )
    return {"status": "completed", "view": view.model_dump(mode="json")}


@router.post("", response_model=DashboardRun, status_code=status.HTTP_201_CREATED)
def create_dashboard_run(
    request: DashboardRunCreateRequest,
    response: Response,
    _auth_context: AuthContext = Depends(require_auth_context),
) -> DashboardRun:
    _require_dashboard_run_membership(_auth_context, request.organization_id)
    settings = Settings()
    if settings.data_source == "snowflake":
        run = _create_snowflake_dashboard_run(request, settings, response)
    else:
        run = _create_demo_dashboard_run(request)
    _record_dashboard_run_created(run)
    return run


class CachedDashboardRunResponse(BaseModel):
    run: DashboardRun
    cached_as_of: datetime


# Retention for a snapshot loaded from the durable cache into the in-memory
# repo. The durable cache TTL (in the run-cache store) governs freshness; this
# in-memory snapshot only needs to outlive a single browsing session so /view
# keeps resolving. A modest 1-day retention caps how long a leaked/abandoned
# snapshot can linger in memory (the previous 90d retention meant each snapshot
# was pinned for 90 days).
_CACHED_SNAPSHOT_RETENTION_DAYS = 1


# Registry bounding in-memory cached snapshots to at most ONE live snapshot per
# org per distinct cached run. Maps org_id -> (cached completed_at, snapshot
# run_id). On a /cached hit for the same underlying cached run (same
# completed_at) whose snapshot is still live in the repo, the existing run_id is
# reused; otherwise the org's previous snapshot is evicted before a new one is
# created. Guarded by its own lock so concurrent /cached reads cannot race.
_cached_snapshot_lock = RLock()
_cached_snapshot_registry: dict[str, tuple[datetime, UUID]] = {}


def _reset_cached_snapshot_registry() -> None:
    """Test hook: clear the per-org cached-snapshot registry."""
    with _cached_snapshot_lock:
        _cached_snapshot_registry.clear()


@router.get("/cached", response_model=CachedDashboardRunResponse)
def read_cached_dashboard_run(
    organization_id: UUID,
    auth_context: AuthContext = Depends(require_auth_context),
) -> Response | CachedDashboardRunResponse:
    _require_dashboard_run_membership(auth_context, organization_id)

    org_id = str(organization_id)
    settings = read_cache_settings(org_id, get_cache_settings_store())
    if not settings.cache_enabled:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    store = get_run_cache_store()
    if store is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    try:
        cached = store.get_active(org_id)
    except RunCacheStoreError:
        # A read failure must not surface as a 500; treat as a cache miss so the
        # client falls back to a fresh run.
        logger.exception("Cache read failed for org %s", org_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if cached is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Connection fingerprint check: the cache row is keyed only by org, so if the
    # owner swapped or disconnected the org's Snowflake connection, the cached
    # datasets belong to the old active connection. Compare the cached fingerprint
    # against the org's current ACTIVE account locator from membership lookup; a
    # missing, inactive, or mismatched connection is a cache miss.
    current_locator = _current_org_account_locator(auth_context, organization_id)
    if cached.account_locator is None or current_locator is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if cached.account_locator != current_locator:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Load the cached snapshot into the in-memory repo via the SAME mechanism
    # demo mode uses, so the returned run's /view resolves for any window.
    # Bound in-memory growth to at most ONE live snapshot per org per distinct
    # cached run: repeated /cached hits for the same underlying run reuse the
    # existing snapshot instead of allocating (and 90-day-pinning) a new one.
    run = _load_or_reuse_cached_snapshot(organization_id, cached)
    return CachedDashboardRunResponse(run=run, cached_as_of=cached.completed_at)


def _load_or_reuse_cached_snapshot(
    organization_id: UUID, cached: CachedDashboardRun
) -> DashboardRun:
    """Return a live in-memory snapshot for the org's cached run, creating one
    only when necessary and never accumulating stale snapshots.

    Reuses the org's existing snapshot when it is for the SAME cached run (same
    ``completed_at``) and still resolvable in the repo; otherwise evicts the
    org's previous snapshot and creates a fresh one. Serialized per process so
    concurrent /cached reads cannot each allocate a snapshot for the same run.
    """
    org_id = str(organization_id)
    with _cached_snapshot_lock:
        existing = _cached_snapshot_registry.get(org_id)
        if existing is not None:
            prev_completed_at, prev_run_id = existing
            if prev_completed_at == cached.completed_at:
                live = dashboard_run_repository.get_run(prev_run_id)
                if live is not None and live.status == "completed":
                    return live
            # Stale or gone: drop the org's previous snapshot before creating a
            # new one so at most one snapshot per org survives.
            dashboard_run_repository.delete_run(prev_run_id)

        run = dashboard_run_repository.create_completed_snapshot(
            organization_id=organization_id,
            source=cached.source,
            window_days=cached.window_days,
            summary=cached.summary,
            datasets=cached.datasets,
            metadata=cached.metadata,
            retention_days=_CACHED_SNAPSHOT_RETENTION_DAYS,
        )
        _cached_snapshot_registry[org_id] = (cached.completed_at, UUID(run.id))
        return run


@router.get("/{run_id}", response_model=DashboardRun)
def read_dashboard_run(
    run_id: UUID,
    auth_context: AuthContext = Depends(require_auth_context),
) -> DashboardRun:
    run = dashboard_run_repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Dashboard run not found")
    _require_dashboard_run_membership(auth_context, run.organization_id)
    return run


@router.get("/{run_id}/view", response_model=DashboardViewResponse)
def read_dashboard_run_view(
    run_id: UUID,
    window_days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    auth_context: AuthContext = Depends(require_auth_context),
) -> dict[str, Any]:
    run = dashboard_run_repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Dashboard view not found")
    _require_dashboard_run_membership(auth_context, run.organization_id)
    view_inputs = dashboard_run_repository.get_view_inputs(run_id)
    if view_inputs is None:
        raise HTTPException(status_code=404, detail="Dashboard view not found")
    (
        run,
        datasets,
        metadata,
        source_bounds,
        source_statuses,
        metadata_authoritative,
    ) = view_inputs
    # Normalize every base source key to [] when absent so the view builder
    # never receives missing keys for a still-streaming run. (build_dashboard_view
    # already defaults absent keys via _dataset_rows, but normalizing keeps the
    # running and completed paths identical.)
    datasets = {
        **{key: [] for key in BASE_RUN_SOURCE_KEYS},
        **datasets,
    }
    if run.status == "running":
        through_date = _through_date_for(metadata)
        if through_date is None:
            # No through_date yet — fall back to spanning all landed data.
            # Passing explicit start/end bypasses the bounds check in
            # resolve_dashboard_view_range so this never 409s.
            view = _prepared_view_or_http_error(
                run=run,
                datasets=datasets,
                metadata=metadata,
                source_bounds=source_bounds,
                window_days=None,
                start_date=source_bounds.source_start_date,
                end_date=source_bounds.source_end_date,
            )
        else:
            # Validate before clamping — mirror the 422 the completed path
            # raises for unsupported window values.
            if (
                window_days is not None
                and window_days not in SUPPORTED_VIEW_WINDOW_DAYS
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_range",
                        "message": "Unsupported dashboard window_days.",
                    },
                )
            # Clamp the requested (or default 30-day) window to available
            # provisional bounds so we never 409 on narrow data while still
            # honoring the caller's range preference.
            wd = window_days if window_days is not None else DEFAULT_VIEW_WINDOW_DAYS
            effective_end = min(through_date, source_bounds.source_end_date)
            effective_start = max(
                window_start_for(effective_end, wd),
                source_bounds.source_start_date,
            )
            view = _prepared_view_or_http_error(
                run=run,
                datasets=datasets,
                metadata=metadata,
                source_bounds=source_bounds,
                window_days=None,
                start_date=effective_start,
                end_date=effective_end,
            )
            view = view.model_copy(
                update={
                    "range": DashboardViewRange(
                        mode="relative",
                        window_days=wd,
                        start_date=effective_start,
                        end_date=effective_end,
                    )
                }
            )
        view = view.model_copy(
            update={"section_statuses": compute_section_statuses(source_statuses or {})}
        )
        _record_dashboard_run_view_retrieved(view)
        return view.model_dump(mode="json")

    view = _prepared_view_or_http_error(
        run=run,
        datasets=datasets,
        metadata=metadata,
        source_bounds=source_bounds,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )
    if source_statuses is not None:
        # Completed/failed views surface the FINAL per-source readiness (not the
        # all-ready model defaults), so an unavailable source stays visible.
        # Crucially, status is reconciled against GROUP AVAILABILITY recorded in
        # the finalized metadata: a gating source whose source GROUP collapsed
        # (e.g. account_usage.available is False after _group_from_outcomes
        # zeroed the whole group) must read "unavailable" even if its streamed
        # state still says "ready". A present-but-empty time window does NOT
        # collapse the group, so it stays "ready".
        # The reconciliation only downgrades when metadata is AUTHORITATIVE
        # (stored/finalized): for a completed snapshot whose stored metadata was
        # None, metadata is reconstructed from rows and carries no real collapse
        # signal, so the streamed/seeded statuses are trusted as-is.
        effective_statuses = _reconcile_completed_source_statuses(
            source_statuses, metadata, authoritative=metadata_authoritative
        )
        # On a settled view the data_mode is known, so the overview's gating basis
        # is resolved by mode: billed/demo gate on org_spend_daily (Organization
        # Usage), estimated on service_spend_daily (Account Usage). This is read
        # from the (authoritative or reconstructed) metadata so a billed overview
        # is not wrongly downgraded when Account Usage collapsed but Organization
        # Usage succeeded, and vice versa.
        completed_data_mode = _data_mode_for(metadata)
        view = view.model_copy(
            update={
                "section_statuses": compute_section_statuses(
                    effective_statuses, data_mode=completed_data_mode
                )
            }
        )
    _record_dashboard_run_view_retrieved(view)
    return view.model_dump(mode="json")


def _metadata_field(metadata: Any, field_name: str) -> Any:
    """Read a metadata field DEFENSIVELY whether metadata is a dict, a typed
    object, or None. Returns None for missing/None metadata or a missing field."""
    if isinstance(metadata, dict):
        return metadata.get(field_name)
    if metadata is not None:
        return getattr(metadata, field_name, None)
    return None


def _data_mode_for(metadata: Any) -> str | None:
    """Read the finalized ``data_mode`` (None → streaming OR-semantics fallback)."""
    value = _metadata_field(metadata, "data_mode")
    return value if isinstance(value, str) else None


def _group_is_available(metadata: Any, group_field: str) -> bool:
    """Read a source group's finalized availability flag DEFENSIVELY.

    ``metadata`` is normally the run's stored metadata dict
    (``DashboardDatasetMetadata.model_dump()``) or None, but this also tolerates
    a typed ``DashboardDatasetMetadata`` (getattr fallback). The result is True
    UNLESS the group's availability flag is explicitly False — missing/None
    metadata, a missing group field, or a missing/None ``available`` value all
    DEFAULT to available so a missing signal never force-collapses a section and
    never crashes.
    """
    group = None
    if isinstance(metadata, dict):
        group = metadata.get(group_field)
    elif metadata is not None:
        group = getattr(metadata, group_field, None)
    if group is None:
        return True
    if isinstance(group, dict):
        available = group.get("available", True)
    else:
        available = getattr(group, "available", True)
    # Treat as available unless EXPLICITLY False (None/missing => available).
    return available is not False


def _reconcile_completed_source_statuses(
    source_statuses: dict[str, str],
    metadata: Any,
    *,
    authoritative: bool = True,
) -> dict[str, str]:
    """Reconcile streamed per-source status against finalized GROUP availability.

    For a settled (completed/terminal) snowflake view, a source that gates a
    section (its dataset key is a SECTION_SOURCE_DEPENDENCIES value) is treated
    as "unavailable" for the section-status rollup ONLY when its source GROUP
    collapsed — i.e. the metadata availability flag for the source's group
    (``GATING_SOURCE_GROUP`` → ``account_usage`` / ``organization_usage``) is
    explicitly False. This is the authoritative collapse signal from
    ``_group_from_outcomes`` (which zeroes an entire group when a member fails);
    an empty dataset alone is NOT, because a legitimately empty time window
    leaves the group available, so a present-but-empty gating source keeps its
    streamed "ready" status.

    The downgrade applies ONLY when ``authoritative`` is True — i.e. ``metadata``
    came from STORED (finalized) metadata, which is the real collapse signal. For
    a completed snapshot whose stored metadata was None, the route reconstructs
    metadata from dataset rows; that reconstruction has no genuine group-collapse
    information (an empty-but-present gating dataset would otherwise look
    collapsed), so when ``authoritative`` is False this is a NO-OP and the
    streamed/seeded ``source_statuses`` are returned unchanged. The route never
    passes None metadata to this helper, so the ``authoritative=False`` branch —
    not None-defensiveness — is what protects the reconstructed path.

    ``metadata`` is the run's stored metadata dict (or None / a typed object) and
    is still read defensively when authoritative: a missing/None metadata or
    missing availability flag DEFAULTS to available, so streamed statuses are
    preserved unchanged and a missing signal never force-collapses a section.
    Non-gating sources are never altered.
    """
    if not authoritative:
        return dict(source_statuses)
    return {
        key: (
            "unavailable"
            if key in GATING_SOURCE_GROUP
            and not _group_is_available(metadata, GATING_SOURCE_GROUP[key])
            else status_value
        )
        for key, status_value in source_statuses.items()
    }


@router.get("/{run_id}/datasets", response_model=DashboardDatasetResponse)
def read_dashboard_run_datasets(
    run_id: UUID,
    auth_context: AuthContext = Depends(require_auth_context),
) -> DashboardDatasetResponse:
    run = dashboard_run_repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Dashboard datasets not found")
    _require_dashboard_run_membership(auth_context, run.organization_id)
    response = dashboard_run_repository.get_dataset_response(run_id)
    if response is None:
        raise HTTPException(status_code=404, detail="Dashboard datasets not found")
    _record_dashboard_run_dataset_retrieved(response)
    return response


@router.post("/{run_id}/sources/{source_id}", status_code=status.HTTP_202_ACCEPTED)
def trigger_dashboard_source(
    run_id: UUID,
    source_id: str,
    auth_context: AuthContext = Depends(require_auth_context),
) -> dict[str, Any]:
    if source_id not in DEFERRED_SOURCES:
        raise HTTPException(status_code=404, detail="Unknown deferred source")
    run = dashboard_run_repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Dashboard run not found")
    _require_dashboard_run_membership(auth_context, run.organization_id)

    if not dashboard_run_repository.claim_source(run_id, source_id):
        # Already pending/completed, or run not completed/expired/deleted.
        state = dashboard_run_repository.get_source_state(run_id, source_id)
        return {"status": state or "unavailable"}

    settings = Settings()
    try:
        rows, skipped = _run_deferred_source(source_id, run, settings)
    except HTTPException:
        dashboard_run_repository.fail_source(run_id, source_id)
        raise
    except Exception:
        dashboard_run_repository.fail_source(run_id, source_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Deferred source fetch failed",
        ) from None
    dashboard_run_repository.complete_source(
        run_id,
        source_id,
        rows=rows,
        partial=bool(skipped),
        skipped_branches=skipped,
    )
    if run.source == "snowflake":
        _update_cached_run_datasets(run, source_id, rows)
    return {"status": "completed"}


def _update_cached_run_datasets(
    run: DashboardRun, source_id: str, rows: list[dict[str, Any]]
) -> None:
    """Fold a freshly-fetched deferred source into the org's durable cache row.

    Deferred sources (e.g. AI consumption) are fetched after the main run
    finalizes, so they were never in the datasets persisted to the cache. Once
    fetched, persist them so FUTURE cache hits include them and never re-query
    Snowflake. Best-effort: any failure is logged and swallowed, never failing
    the request. The cache row is updated only when it actually corresponds to
    this run — either the deferred source was triggered on the ORIGINAL run
    (cached.run_id == run.id) or on a cache-hit snapshot whose registry entry
    maps to this run's id.
    """
    try:
        store = get_run_cache_store()
        if store is None:
            return
        if run.organization_id is None:
            return
        org_id = str(run.organization_id)
        cached = store.get_active(org_id)
        if cached is None:
            return
        if not _cached_row_matches_run(cached, org_id, run.id):
            return
        store.update_datasets_if_current(cached, {**cached.datasets, source_id: rows})
    except Exception:  # noqa: BLE001 — cache update must never fail the request
        logger.exception(
            "Failed to fold deferred source %s into cache for run %s",
            source_id,
            run.id,
        )


def _cached_row_matches_run(
    cached: CachedDashboardRun, org_id: str, run_id: str
) -> bool:
    if cached.run_id == run_id:
        return True
    with _cached_snapshot_lock:
        entry = _cached_snapshot_registry.get(org_id)
    return entry is not None and str(entry[1]) == run_id


@router.get("/{run_id}/sources/{source_id}")
def read_dashboard_source(
    run_id: UUID,
    source_id: str,
    window_days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    auth_context: AuthContext = Depends(require_auth_context),
) -> dict[str, Any]:
    if source_id not in DEFERRED_SOURCES:
        raise HTTPException(status_code=404, detail="Unknown deferred source")
    run = dashboard_run_repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Dashboard run not found")
    _require_dashboard_run_membership(auth_context, run.organization_id)

    state = dashboard_run_repository.get_source_state(run_id, source_id) or "idle"
    if state != "completed":
        return {"status": state}

    view_inputs = dashboard_run_repository.get_view_inputs(run_id)
    if view_inputs is None:
        return {"status": "expired"}
    (
        _run,
        datasets,
        metadata,
        source_bounds,
        _source_statuses,
        _metadata_authoritative,
    ) = view_inputs
    meta = dashboard_run_repository.get_source_meta(run_id, source_id) or {}

    through_date = _through_date_for(metadata) or source_bounds.source_end_date
    view_range = _resolve_source_view_range_or_http_error(
        through_date=through_date,
        source_start_date=source_bounds.source_start_date,
        source_end_date=source_bounds.source_end_date,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )
    view = build_ai_detail_view(
        ai_rows=datasets.get(source_id, []),
        rate_rows=datasets.get("rate_sheet_daily", []),
        currency=metadata.currency or "USD",
        estimated_credit_price_usd=metadata.estimated_credit_price_usd,
        start_date=view_range.start_date,
        end_date=view_range.end_date,
        partial=bool(meta.get("partial")),
        skipped_branches=list(meta.get("skipped_branches", [])),
    )
    return {"status": "completed", "view": view.model_dump(mode="json")}


@router.delete("/{run_id}", response_model=DashboardRun)
def delete_dashboard_run(
    run_id: UUID,
    auth_context: AuthContext = Depends(require_auth_context),
) -> DashboardRun:
    run = dashboard_run_repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Dashboard run not found")
    _require_dashboard_run_membership(auth_context, run.organization_id)
    run = dashboard_run_repository.delete_run(run_id)
    _record_dashboard_run_deleted(run)
    return run


def _record_dashboard_run_created(run: DashboardRun) -> None:
    audit_event_recorder.record_org_event(
        "dashboard_run.created",
        organization_id=run.organization_id,
        payload={
            "run_id": run.id,
            "source": run.source,
            "status": run.status,
            "window_days": run.window_days,
            "dataset_keys": _dataset_keys_for_run(run.id),
        },
    )


def _record_dashboard_run_dataset_retrieved(
    response: DashboardDatasetResponse,
) -> None:
    audit_event_recorder.record_org_event(
        "dashboard_run.dataset_retrieved",
        organization_id=response.run.organization_id,
        payload={
            "run_id": response.run.id,
            "dataset_keys": sorted(response.datasets),
        },
    )


def _record_dashboard_run_view_retrieved(response: DashboardViewResponse) -> None:
    audit_event_recorder.record_org_event(
        "dashboard_run.view_retrieved",
        organization_id=response.run.organization_id,
        payload={
            "run_id": response.run.id,
            "range_mode": response.range.mode,
            "start_date": response.range.start_date.isoformat(),
            "end_date": response.range.end_date.isoformat(),
            "window_days": response.range.window_days,
        },
    )


def _record_dashboard_run_deleted(run: DashboardRun) -> None:
    audit_event_recorder.record_org_event(
        "dashboard_run.deleted",
        organization_id=run.organization_id,
        payload={"run_id": run.id, "status": run.status},
    )


def _dataset_keys_for_run(run_id: str) -> list[str]:
    try:
        parsed_run_id = UUID(run_id)
    except ValueError:
        return []
    response = dashboard_run_repository.get_dataset_response(parsed_run_id)
    if response is None:
        return []
    return sorted(response.datasets)


def _source_bounds_for_dataset_rows(
    datasets: dict[str, list[dict[str, Any]]],
) -> StoredSourceBounds:
    usage_dates = _usage_dates_for_dataset_rows(datasets)
    if not usage_dates:
        now = datetime.now(timezone.utc).date()
        return StoredSourceBounds(source_start_date=now, source_end_date=now)
    return StoredSourceBounds(
        source_start_date=min(usage_dates),
        source_end_date=max(usage_dates),
    )


def _usage_dates_for_dataset_rows(
    datasets: dict[str, list[dict[str, Any]]],
) -> list[date]:
    return [
        _as_usage_date(row["usage_date"])
        for rows in datasets.values()
        for row in rows
        if row.get("usage_date") is not None
    ]


def _as_usage_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return datetime.fromisoformat(str(value)).date()


def _metadata_for_dataset_rows(
    datasets: dict[str, list[dict[str, Any]]],
) -> DashboardDatasetMetadata:
    settings = Settings()
    org_spend_rows = datasets.get("org_spend_daily", [])
    account_usage_rows = [
        row
        for dataset_key in ACCOUNT_USAGE_DATASET_KEYS
        for row in datasets.get(dataset_key, [])
    ]
    org_currencies = {
        str(row["currency"])
        for row in org_spend_rows
        if row.get("currency") is not None
    }
    unsupported_reason = "mixed_currency" if len(org_currencies) > 1 else None
    data_mode = "billed" if org_spend_rows else "estimated"
    currency = (
        None
        if unsupported_reason
        else _currency_for_reconstructed_metadata(data_mode, org_currencies)
    )
    return DashboardDatasetMetadata(
        data_mode=data_mode,
        account_locator=_account_locator_for_dataset_rows(datasets),
        currency=currency,
        billing_through_date=_max_usage_date(org_spend_rows)
        if org_spend_rows
        else None,
        account_usage_through_date=_max_usage_date(account_usage_rows)
        if account_usage_rows
        else None,
        estimated_credit_price_usd=settings.estimated_credit_price_usd,
        storage_price_usd_per_tb_month=settings.storage_price_usd_per_tb_month,
        unsupported_reason=unsupported_reason,
        organization_usage=SourceAvailability(available=bool(org_spend_rows)),
        account_usage=SourceAvailability(available=bool(account_usage_rows)),
    )


def _currency_for_reconstructed_metadata(
    data_mode: str, org_currencies: set[str]
) -> str:
    if data_mode == "billed" and len(org_currencies) == 1:
        return next(iter(org_currencies))
    return "USD"


def _max_usage_date(rows: list[dict[str, Any]]) -> date | None:
    usage_dates = [
        _as_usage_date(row["usage_date"]) for row in rows if row.get("usage_date")
    ]
    return max(usage_dates) if usage_dates else None


def _account_locator_for_dataset_rows(
    datasets: dict[str, list[dict[str, Any]]],
) -> str | None:
    for row in datasets.get("current_account", []):
        account_locator = row.get("account_locator")
        if account_locator is not None:
            return str(account_locator)
    return None


def _account_locator_from_metadata(metadata: Any) -> str | None:
    """The Snowflake account fingerprint persisted with the cached run so a later
    /cached read can reject a stale connection. None → "no fingerprint"."""
    value = _metadata_field(metadata, "account_locator")
    return str(value) if value is not None else None


def _current_org_account_locator(
    auth_context: AuthContext, organization_id: UUID
) -> str | None:
    """The org's CURRENT Snowflake account_locator from the membership lookup.

    Returns None when the org is not found in the auth context or has no
    connected account (disconnected). A None here that differs from a cached
    row's fingerprint is treated as a cache miss by the /cached route.
    """
    target = str(organization_id)
    for org in auth_context.organizations:
        if str(org.id) == target:
            connection_status = getattr(org, "connection_status", None)
            if connection_status is not None and connection_status != "active":
                return None
            return org.account_locator
    return None


def _resolve_source_view_range_or_http_error(
    *,
    through_date: date,
    source_start_date: date,
    source_end_date: date,
    window_days: int | None,
    start_date: date | None,
    end_date: date | None,
):
    """Resolve a deferred-source view range, translating range errors to HTTP
    422/409 the same way ``_prepared_view_or_http_error`` does for ``/view``."""
    try:
        return resolve_dashboard_view_range(
            through_date=through_date,
            source_start_date=source_start_date,
            source_end_date=source_end_date,
            window_days=window_days,
            start_date=start_date,
            end_date=end_date,
        )
    except DashboardRangeOutOfBoundsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "range_out_of_bounds",
                "message": "Broader date ranges are not supported yet.",
                "source_start_date": exc.source_start_date.isoformat(),
                "source_end_date": exc.source_end_date.isoformat(),
            },
        ) from None
    except DashboardInvalidRangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_range", "message": str(exc)},
        ) from None


def _prepared_view_or_http_error(
    *,
    run: DashboardRun,
    datasets: dict[str, list[dict[str, Any]]],
    metadata: DashboardDatasetMetadata,
    source_bounds: StoredSourceBounds,
    window_days: int | None,
    start_date: date | None,
    end_date: date | None,
) -> DashboardViewResponse:
    try:
        return build_dashboard_view(
            run=run,
            datasets=datasets,
            metadata=metadata,
            source_start_date=source_bounds.source_start_date,
            source_end_date=source_bounds.source_end_date,
            window_days=window_days,
            start_date=start_date,
            end_date=end_date,
        )
    except DashboardRangeOutOfBoundsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "range_out_of_bounds",
                "message": "Broader date ranges are not supported yet.",
                "source_start_date": exc.source_start_date.isoformat(),
                "source_end_date": exc.source_end_date.isoformat(),
            },
        ) from None
    except DashboardInvalidRangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_range", "message": str(exc)},
        ) from None


def _persist_completed_run_to_cache(
    run_id: UUID,
    *,
    summary: dict[str, Any],
    metadata: dict[str, Any] | None,
    datasets: dict[str, list[dict[str, Any]]],
) -> None:
    """Write a finalized Snowflake run to the durable per-org cache.

    Best-effort: any failure (no store, cache disabled resolution error, network
    write failure) is logged and swallowed — the run has already finalized and
    must not be affected. Demo runs never reach this path (only the snowflake
    worker calls it), keeping the demo bypass out of the cache.
    """
    try:
        store = get_run_cache_store()
        if store is None:
            return

        run = dashboard_run_repository.get_run(run_id)
        if run is None or run.organization_id is None:
            return
        org_id = str(run.organization_id)

        settings = read_cache_settings(org_id, get_cache_settings_store())
        if not settings.cache_enabled:
            return

        completed_at = run.completed_at or datetime.now(timezone.utc)
        expires_at = completed_at + timedelta(seconds=settings.cache_ttl_seconds)
        bounds = _source_bounds_for_dataset_rows(datasets)

        store.upsert(
            CachedDashboardRun(
                organization_id=org_id,
                run_id=run.id,
                source=run.source,
                window_days=run.window_days,
                account_locator=_account_locator_from_metadata(metadata),
                summary=summary,
                metadata=metadata,
                datasets=datasets,
                source_start_date=bounds.source_start_date,
                source_end_date=bounds.source_end_date,
                completed_at=completed_at,
                expires_at=expires_at,
            )
        )
    except Exception:  # noqa: BLE001 — cache write must never fail the run
        logger.exception("Failed to persist run %s to dashboard cache", run_id)


def _run_dashboard_worker(
    run_id: UUID,
    settings: Settings,
    connection_config: Any,
    summary_window_days: int,
) -> None:
    """Drive the parallel run to completion, streaming each dataset as it lands.

    Wrapped so the run ALWAYS reaches a terminal state — a crash mid-fetch
    finalizes ``failed`` rather than leaving the run stuck ``running``. The
    ``on_outcome`` hook fires from worker threads as each base source lands; it
    only touches the lock-guarded repo, so it is cheap and thread-safe.
    """
    repo = dashboard_run_repository

    def on_outcome(outcome: SourceOutcome) -> None:
        if outcome.available and outcome.rows is not None:
            repo.set_dataset(run_id, outcome.key, outcome.rows)
            repo.complete_source(run_id, outcome.key)
        else:
            repo.fail_source(run_id, outcome.key, error="unavailable")

    try:
        data = build_snowflake_dashboard_data(
            settings,
            summary_window_days=summary_window_days,
            connection_config=connection_config,
            on_source_outcome=on_outcome,
        )
        # The success finalize (model_dump/finalize_run) is INSIDE the guarded
        # block: a crash here must still finalize the run terminal rather than
        # strand it "running" until the wall-clock TTL.
        repo.finalize_run(
            run_id,
            status="completed",
            summary=data.summary,
            metadata=data.metadata.model_dump(mode="json"),
            datasets=data.datasets,
        )
        # Best-effort durable cache write. A failure here must NEVER fail the run
        # (which already finalized above), so it is caught and logged inside the
        # helper — it must not reach the outer terminal-state except clause.
        _persist_completed_run_to_cache(
            run_id,
            summary=data.summary,
            metadata=data.metadata.model_dump(mode="json"),
            datasets=data.datasets,
        )
    except DashboardSourcesUnavailableError:
        repo.finalize_run(
            run_id,
            status="failed",
            summary={},
            metadata=None,
            datasets={},
            error="Could not query Snowflake billing or Account Usage data.",
        )
    except Exception:  # noqa: BLE001 — terminal-state guarantee
        # The raw exception detail must never reach the user-facing run.error
        # field (it is serialized on every DashboardRun GET). Log it
        # server-side and store only a neutral message. This also covers a crash
        # in the success finalize above.
        logger.exception("Dashboard run %s failed unexpectedly", run_id)
        repo.finalize_run(
            run_id,
            status="failed",
            summary={},
            metadata=None,
            datasets={},
            error="An unexpected error occurred while building the dashboard.",
        )


def _create_snowflake_dashboard_run(
    request: DashboardRunCreateRequest,
    settings: Settings,
    response: Response,
) -> DashboardRun:
    from app.services.org_connection_resolver import (
        OrgConnectionNotConfiguredError,
        resolve_snowflake_config,
    )
    from app.services.snowflake_runtime import get_connection_fetcher

    # Resolve the connection synchronously so a missing connection still 409s on
    # POST (rather than failing asynchronously inside the worker).
    try:
        connection_config = resolve_snowflake_config(
            str(request.organization_id),
            settings,
            fetch_connection=get_connection_fetcher(settings),
        )
    except OrgConnectionNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This organization has no Snowflake connection configured.",
        ) from None

    run = dashboard_run_repository.create_running_run(
        organization_id=request.organization_id,
        source=request.source,
        # Persist the Snowflake fetch window used for datasets.
        window_days=FETCH_WINDOW_DAYS,
        expected_sources=BASE_RUN_SOURCE_KEYS,
        retention_days=request.retention_days,
    )
    response.status_code = status.HTTP_202_ACCEPTED
    Thread(
        target=_run_dashboard_worker,
        args=(UUID(run.id), settings, connection_config, request.window_days),
        daemon=True,
    ).start()
    return run


def _run_deferred_source(
    source_id: str, run: DashboardRun, settings: Settings
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve the run's org connection and run the deferred source's fetch.

    Resolution mirrors `_create_snowflake_dashboard_run` but is kept inline
    (rather than extracted into a shared helper) so the proven create-run path
    is left untouched. The `execute` closure references
    `dashboard_datasets.execute_source_query` at call time — the same module
    attribute the main-run executor binds — so the fetch can be stubbed in tests.
    """
    from app.services import dashboard_datasets
    from app.services.org_connection_resolver import (
        OrgConnectionNotConfiguredError,
        resolve_snowflake_config,
    )
    from app.services.snowflake_runtime import get_connection_fetcher

    deferred = DEFERRED_SOURCES[source_id]
    try:
        connection_config = resolve_snowflake_config(
            str(run.organization_id),
            settings,
            fetch_connection=get_connection_fetcher(settings),
        )
    except OrgConnectionNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This organization has no Snowflake connection configured.",
        ) from None

    def execute(sql: str, bind_params: dict[str, Any]) -> list[dict[str, Any]]:
        return dashboard_datasets.execute_source_query(
            sql, bind_params, connection_config
        )

    return deferred.fetch(execute, FETCH_WINDOW_DAYS)


def _create_demo_dashboard_run(request: DashboardRunCreateRequest) -> DashboardRun:
    if request.datasets:
        return dashboard_run_repository.create_completed_run(request)

    demo_payload = build_demo_dashboard_dataset()
    return dashboard_run_repository.create_completed_snapshot(
        organization_id=request.organization_id,
        source=request.source,
        window_days=demo_payload.run.window_days,
        summary=demo_payload.summary.model_dump(mode="json"),
        datasets=demo_payload.datasets,
        metadata=demo_payload.metadata.model_dump(mode="json"),
        retention_days=request.retention_days,
    )

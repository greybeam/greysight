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
    DashboardInvalidRangeError,
    DashboardRangeOutOfBoundsError,
    _through_date_for,
    build_ai_detail_view,
    build_dashboard_view,
    resolve_dashboard_view_range,
)
from app.services.dashboard_view_models import DashboardViewResponse
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
        ]
        | None
    ):
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
            return (
                run,
                dataset_rows,
                DashboardDatasetMetadata.model_validate(metadata)
                if metadata is not None
                else _metadata_for_dataset_rows(dataset_rows),
                source_bounds,
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
            self._source_states.setdefault(run_id, {})[source_id] = {
                "status": "ready" if run.status == "running" else "completed",
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
            state: dict[str, Any] = {
                "status": "unavailable" if run is not None and run.status == "running" else "failed"
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

    def set_dataset(
        self, run_id: UUID, key: str, rows: list[dict[str, Any]]
    ) -> None:
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
            self._runs[run_id] = run.model_copy(
                update={
                    "status": status,
                    "completed_at": now,
                    "updated_at": now,
                    "error": error,
                }
            )
            self._running_deadlines.pop(run_id, None)
            retention_days = self._retention_days.get(run_id, 7)
            expires_at = now + timedelta(days=retention_days)
            self._summaries[run_id] = summary
            self._metadata[run_id] = metadata
            self._datasets[run_id] = {
                key: StoredDashboardDataset(
                    aggregate_dataset=rows, retention_expires_at=expires_at
                )
                for key, rows in datasets.items()
            }
            self._store_source_bounds(run_id, datasets)


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
    run, datasets, metadata, source_bounds = view_inputs
    view = _prepared_view_or_http_error(
        run=run,
        datasets=datasets,
        metadata=metadata,
        source_bounds=source_bounds,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )
    _record_dashboard_run_view_retrieved(view)
    return view.model_dump(mode="json")


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
    return {"status": "completed"}


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
    _run, datasets, metadata, source_bounds = view_inputs
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
    except DashboardSourcesUnavailableError:
        repo.finalize_run(
            run_id,
            status="failed",
            summary={},
            metadata=None,
            datasets={},
            error="Could not query Snowflake billing or Account Usage data.",
        )
        return
    except Exception:  # noqa: BLE001 — terminal-state guarantee
        # The raw exception detail must never reach the user-facing run.error
        # field (it is serialized on every DashboardRun GET). Log it
        # server-side and store only a neutral message.
        logger.exception("Dashboard run %s failed unexpectedly", run_id)
        repo.finalize_run(
            run_id,
            status="failed",
            summary={},
            metadata=None,
            datasets={},
            error="An unexpected error occurred while building the dashboard.",
        )
        return
    repo.finalize_run(
        run_id,
        status="completed",
        summary=data.summary,
        metadata=data.metadata.model_dump(mode="json"),
        datasets=data.datasets,
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

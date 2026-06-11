from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import AuthContext, require_auth_context, require_org_membership
from app.config import Settings
from app.models import (
    DashboardDatasetMetadata,
    DashboardDatasetResponse,
    DashboardRun,
    DashboardRunCreateRequest,
)
from app.services.audit_events import audit_event_recorder
from app.services.dashboard_datasets import (
    FETCH_WINDOW_DAYS,
    DashboardSourcesUnavailableError,
    build_snowflake_dashboard_data,
)
from app.services.demo_data import build_demo_dashboard_dataset

router = APIRouter(prefix="/api/dashboard-runs", tags=["dashboard-runs"])


class StoredDashboardDataset(BaseModel):
    aggregate_dataset: list[dict[str, Any]]
    retention_expires_at: datetime


class InMemoryDashboardRunRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._runs: dict[UUID, DashboardRun] = {}
        self._summaries: dict[UUID, dict[str, Any]] = {}
        self._datasets: dict[UUID, dict[str, StoredDashboardDataset]] = {}
        self._metadata: dict[UUID, dict[str, Any] | None] = {}

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._summaries.clear()
            self._datasets.clear()
            self._metadata.clear()

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
        return run

    def get_run(self, run_id: UUID) -> DashboardRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def get_dataset_response(self, run_id: UUID) -> DashboardDatasetResponse | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.status == "deleted":
                return None

            stored_datasets = self._datasets.get(run_id)
            if stored_datasets is None:
                return None
            if any(
                dataset_is_expired(dataset.retention_expires_at)
                for dataset in list(stored_datasets.values())
            ):
                expired_run = run.model_copy(
                    update={
                        "status": "expired",
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                self._runs[run_id] = expired_run
                self._datasets.pop(run_id, None)
                self._metadata.pop(run_id, None)
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
            return deleted_run


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


@router.post("", response_model=DashboardRun, status_code=status.HTTP_201_CREATED)
def create_dashboard_run(
    request: DashboardRunCreateRequest,
    _auth_context: AuthContext = Depends(require_auth_context),
) -> DashboardRun:
    _require_dashboard_run_membership(_auth_context, request.organization_id)
    settings = Settings()
    if settings.data_source == "snowflake":
        run = _create_snowflake_dashboard_run(request, settings)
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


def _create_snowflake_dashboard_run(
    request: DashboardRunCreateRequest, settings: Settings
) -> DashboardRun:
    try:
        dashboard_data = build_snowflake_dashboard_data(
            settings,
            summary_window_days=request.window_days,
        )
    except DashboardSourcesUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not query Snowflake billing or Account Usage data.",
        ) from None

    return dashboard_run_repository.create_completed_snapshot(
        organization_id=request.organization_id,
        source=request.source,
        # Persist the Snowflake fetch window used for datasets.
        # dashboard_data.summary was computed with request.window_days for legacy
        # summary compatibility.
        window_days=FETCH_WINDOW_DAYS,
        summary=dashboard_data.summary,
        datasets=dashboard_data.datasets,
        metadata=dashboard_data.metadata.model_dump(mode="json"),
        retention_days=request.retention_days,
    )


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

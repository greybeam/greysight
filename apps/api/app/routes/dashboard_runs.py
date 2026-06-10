from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.models import DashboardDatasetResponse, DashboardRun, DashboardRunCreateRequest
from app.services.demo_data import build_demo_dashboard_dataset

router = APIRouter(prefix="/api/dashboard-runs", tags=["dashboard-runs"])


class StoredDashboardDataset(BaseModel):
    aggregate_dataset: list[dict[str, Any]]
    retention_expires_at: datetime


class InMemoryDashboardRunRepository:
    def __init__(self) -> None:
        self._runs: dict[UUID, DashboardRun] = {}
        self._summaries: dict[UUID, dict[str, Any]] = {}
        self._datasets: dict[UUID, dict[str, StoredDashboardDataset]] = {}

    def clear(self) -> None:
        self._runs.clear()
        self._summaries.clear()
        self._datasets.clear()

    def create_completed_run(self, request: DashboardRunCreateRequest) -> DashboardRun:
        now = datetime.now(timezone.utc)
        run_id = uuid4()
        run = DashboardRun(
            id=str(run_id),
            organization_id=request.organization_id,
            source=request.source,
            status="completed",
            window_days=request.window_days,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        retention_expires_at = now + timedelta(days=request.retention_days)
        self._runs[run_id] = run
        self._summaries[run_id] = request.summary
        self._datasets[run_id] = {
            dataset_key: StoredDashboardDataset(
                aggregate_dataset=rows,
                retention_expires_at=retention_expires_at,
            )
            for dataset_key, rows in request.datasets.items()
        }
        return run

    def get_run(self, run_id: UUID) -> DashboardRun | None:
        return self._runs.get(run_id)

    def get_dataset_response(self, run_id: UUID) -> DashboardDatasetResponse | None:
        run = self._runs.get(run_id)
        if run is None or run.status == "deleted":
            return None

        stored_datasets = self._datasets.get(run_id)
        if stored_datasets is None:
            return None
        if any(
            dataset_is_expired(dataset.retention_expires_at)
            for dataset in stored_datasets.values()
        ):
            expired_run = run.model_copy(
                update={
                    "status": "expired",
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._runs[run_id] = expired_run
            self._datasets.pop(run_id, None)
            return None

        return DashboardDatasetResponse(
            run=run,
            summary=self._summaries.get(run_id, {}),
            datasets={
                dataset_key: stored_dataset.aggregate_dataset
                for dataset_key, stored_dataset in stored_datasets.items()
            },
        )

    def delete_run(self, run_id: UUID) -> DashboardRun | None:
        run = self._runs.get(run_id)
        if run is None:
            return None

        deleted_run = run.model_copy(
            update={"status": "deleted", "updated_at": datetime.now(timezone.utc)}
        )
        self._runs[run_id] = deleted_run
        self._datasets.pop(run_id, None)
        return deleted_run


dashboard_run_repository = InMemoryDashboardRunRepository()


def dataset_is_expired(expires_at: datetime, *, now: datetime | None = None) -> bool:
    comparison_time = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= comparison_time


@router.get("/demo", response_model=DashboardRun)
def read_demo_dashboard_run() -> DashboardRun:
    demo_run = build_demo_dashboard_dataset().run
    return DashboardRun.model_validate(demo_run.model_dump(mode="json"))


@router.get("/demo/datasets")
def read_demo_dashboard_datasets() -> dict[str, Any]:
    payload = build_demo_dashboard_dataset()
    return payload.model_dump(mode="json")


@router.post("", response_model=DashboardRun, status_code=status.HTTP_201_CREATED)
def create_dashboard_run(request: DashboardRunCreateRequest) -> DashboardRun:
    return dashboard_run_repository.create_completed_run(request)


@router.get("/{run_id}", response_model=DashboardRun)
def read_dashboard_run(run_id: UUID) -> DashboardRun:
    run = dashboard_run_repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Dashboard run not found")
    return run


@router.get("/{run_id}/datasets", response_model=DashboardDatasetResponse)
def read_dashboard_run_datasets(run_id: UUID) -> DashboardDatasetResponse:
    response = dashboard_run_repository.get_dataset_response(run_id)
    if response is None:
        raise HTTPException(status_code=404, detail="Dashboard datasets not found")
    return response


@router.delete("/{run_id}", response_model=DashboardRun)
def delete_dashboard_run(run_id: UUID) -> DashboardRun:
    run = dashboard_run_repository.delete_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Dashboard run not found")
    return run

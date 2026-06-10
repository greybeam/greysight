from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import AuthContext, require_auth_context, require_org_membership
from app.config import Settings
from app.models import DashboardDatasetResponse, DashboardRun, DashboardRunCreateRequest
from app.services.cost_metrics import (
    build_dashboard_summary,
    derive_account_spend_daily,
)
from app.services.audit_events import audit_event_recorder
from app.services.dashboard_registry import load_dashboard_registry
from app.services.demo_data import build_demo_dashboard_dataset
from app.services.snowflake_client import (
    SnowflakeConfigurationError,
    SnowflakeQueryError,
    execute_source_query,
)

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

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._summaries.clear()
            self._datasets.clear()

    def create_completed_run(self, request: DashboardRunCreateRequest) -> DashboardRun:
        return self.create_completed_snapshot(
            organization_id=request.organization_id,
            source=request.source,
            window_days=request.window_days,
            summary=request.summary,
            datasets=request.datasets,
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
                return None

            return DashboardDatasetResponse(
                run=run,
                summary=self._summaries.get(run_id, {}),
                datasets={
                    dataset_key: stored_dataset.aggregate_dataset
                    for dataset_key, stored_dataset in stored_datasets.items()
                },
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
        datasets = _build_snowflake_datasets(request.window_days)
    except (SnowflakeConfigurationError, SnowflakeQueryError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not query Snowflake Account Usage.",
        ) from None

    summary = build_dashboard_summary(
        account_spend_daily=datasets["account_spend_daily"],
        warehouse_spend_daily=datasets["warehouse_spend_daily"],
        database_storage_daily=datasets["database_storage_daily"],
        current_usage_date=datetime.now(timezone.utc).date(),
        window_days=request.window_days,
        storage_price_usd_per_tb_month=settings.storage_price_usd_per_tb_month,
    ).model_dump(mode="json")

    json_ready_datasets = {
        dataset_key: [
            row.model_dump(mode="json") if hasattr(row, "model_dump") else row
            for row in rows
        ]
        for dataset_key, rows in datasets.items()
    }
    return dashboard_run_repository.create_completed_snapshot(
        organization_id=request.organization_id,
        source=request.source,
        window_days=request.window_days,
        summary=summary,
        datasets=json_ready_datasets,
        retention_days=request.retention_days,
    )


def _create_demo_dashboard_run(request: DashboardRunCreateRequest) -> DashboardRun:
    if request.datasets:
        return dashboard_run_repository.create_completed_run(request)

    demo_payload = build_demo_dashboard_dataset()
    return dashboard_run_repository.create_completed_snapshot(
        organization_id=request.organization_id,
        source=request.source,
        window_days=request.window_days,
        summary=demo_payload.summary.model_dump(mode="json"),
        datasets=demo_payload.datasets,
        retention_days=request.retention_days,
    )


def _build_snowflake_datasets(window_days: int) -> dict[str, list[Any]]:
    registry = load_dashboard_registry()
    bind_params = {"window_days": window_days}
    datasets = {
        dataset_key: execute_source_query(source.sql, bind_params)
        for dataset_key, source in registry.sources.items()
    }
    datasets["account_spend_daily"] = derive_account_spend_daily(
        datasets["service_spend_daily"]
    )
    datasets["top_warehouses_table"] = _build_top_warehouses_table(
        datasets["warehouse_spend_daily"]
    )
    return datasets


def _build_top_warehouses_table(
    warehouse_spend_daily: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    credits_by_warehouse: dict[str, float] = {}
    for row in warehouse_spend_daily:
        warehouse_name = str(row["warehouse_name"])
        credits_by_warehouse[warehouse_name] = credits_by_warehouse.get(
            warehouse_name, 0.0
        ) + float(row["credits_used"])
    return [
        {"warehouse_name": warehouse_name, "credits_used": credits_used}
        for warehouse_name, credits_used in sorted(
            credits_by_warehouse.items(), key=lambda item: item[1], reverse=True
        )[:10]
    ]

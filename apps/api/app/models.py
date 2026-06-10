from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from typing import Self

from pydantic import BaseModel, Field, model_validator


DashboardRunSource = Literal["demo", "snowflake"]
DashboardRunStatus = Literal[
    "queued", "running", "completed", "failed", "expired", "deleted"
]
DashboardRunCreateSource = Literal["snowflake"]

SAFE_DATASET_ROW_FIELDS: dict[str, frozenset[str]] = {
    "account_spend_daily": frozenset({"usage_date", "credits_used"}),
    "warehouse_spend_daily": frozenset(
        {"usage_date", "warehouse_name", "credits_used"}
    ),
    "service_spend_daily": frozenset({"usage_date", "service_type", "credits_used"}),
    "query_compute_by_user_daily": frozenset(
        {"usage_date", "user_name", "warehouse_name", "credits_used"}
    ),
    "database_storage_daily": frozenset(
        {
            "usage_date",
            "database_name",
            "average_database_bytes",
            "average_failsafe_bytes",
        }
    ),
    "top_warehouses_table": frozenset({"warehouse_name", "credits_used"}),
}
REQUIRED_DATASET_KEYS = frozenset(SAFE_DATASET_ROW_FIELDS)


class DashboardRun(BaseModel):
    id: str
    status: DashboardRunStatus
    source: DashboardRunSource
    window_days: int = Field(gt=0, le=365)
    organization_id: UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    error_code: str | None = None
    user_safe_message: str | None = None
    error: str | None = None


class DashboardRunCreateRequest(BaseModel):
    organization_id: UUID
    source: DashboardRunCreateSource = "snowflake"
    window_days: int = Field(gt=0, le=365)
    summary: dict[str, Any] = Field(default_factory=dict)
    datasets: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    retention_days: int = Field(default=7, gt=0, le=90)

    @model_validator(mode="after")
    def validate_safe_aggregate_datasets(self) -> Self:
        dataset_keys = set(self.datasets)
        if dataset_keys != REQUIRED_DATASET_KEYS:
            missing_keys = sorted(REQUIRED_DATASET_KEYS - dataset_keys)
            unknown_keys = sorted(dataset_keys - REQUIRED_DATASET_KEYS)
            problems: list[str] = []
            if missing_keys:
                problems.append(f"missing datasets: {', '.join(missing_keys)}")
            if unknown_keys:
                problems.append(f"unknown datasets: {', '.join(unknown_keys)}")
            raise ValueError("; ".join(problems))

        for dataset_key, rows in self.datasets.items():
            safe_fields = SAFE_DATASET_ROW_FIELDS[dataset_key]
            for row_index, row in enumerate(rows):
                row_fields = set(row)
                if row_fields != safe_fields:
                    missing_fields = sorted(safe_fields - row_fields)
                    unknown_fields = sorted(row_fields - safe_fields)
                    problems = []
                    if missing_fields:
                        problems.append(f"missing fields: {', '.join(missing_fields)}")
                    if unknown_fields:
                        problems.append(f"unknown fields: {', '.join(unknown_fields)}")
                    raise ValueError(
                        f"Dataset {dataset_key}[{row_index}] is invalid: "
                        + "; ".join(problems)
                    )
        return self


class DashboardDatasetResponse(BaseModel):
    run: DashboardRun
    summary: dict[str, Any]
    datasets: dict[str, list[dict[str, Any]]]

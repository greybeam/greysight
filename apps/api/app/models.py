from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from typing import Self

from pydantic import BaseModel, Field, model_validator


DashboardRunSource = Literal["demo", "snowflake"]
DashboardRunStatus = Literal[
    "queued", "running", "completed", "failed", "expired", "deleted"
]
DashboardRunCreateSource = Literal["snowflake"]
SCHEMA_VERSION = 1
DashboardDataMode = Literal["demo", "billed", "estimated"]
UnsupportedReason = Literal["mixed_currency"]

SAFE_DATASET_ROW_FIELDS: dict[str, frozenset[str]] = {
    "account_spend_daily": frozenset({"usage_date", "credits_used"}),
    "warehouse_spend_daily": frozenset(
        {
            "usage_date",
            "warehouse_name",
            "credits_used",
            "credits_used_compute",
            "credits_attributed_queries",
        }
    ),
    "service_spend_daily": frozenset({"usage_date", "service_type", "credits_used"}),
    "query_compute_by_user_daily": frozenset(
        {
            "usage_date",
            "user_name",
            "warehouse_name",
            "credits_attributed_compute",
        }
    ),
    "database_storage_daily": frozenset(
        {
            "usage_date",
            "database_name",
            "average_database_bytes",
            "average_failsafe_bytes",
            "average_hybrid_table_storage_bytes",
        }
    ),
    "top_warehouses_table": frozenset({"warehouse_name", "credits_used"}),
    "org_spend_daily": frozenset(
        {
            "usage_date",
            "service_type",
            "rating_type",
            "billing_type",
            "is_adjustment",
            "currency",
            "spend",
        }
    ),
    "rate_sheet_daily": frozenset(
        {
            "usage_date",
            "service_type",
            "usage_type",
            "rating_type",
            "currency",
            "effective_rate",
        }
    ),
    "capacity_balance_daily": frozenset({"usage_date", "currency", "balance"}),
    "current_account": frozenset({"account_locator"}),
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
    organization_id: UUID | None = None
    source: DashboardRunCreateSource = "snowflake"
    window_days: int = Field(gt=0, le=365)
    summary: dict[str, Any] = Field(default_factory=dict)
    datasets: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    retention_days: int = Field(default=7, gt=0, le=90)

    @model_validator(mode="after")
    def validate_safe_aggregate_datasets(self) -> Self:
        dataset_keys = set(self.datasets)
        if not dataset_keys:
            return self
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
                if "usage_date" in row:
                    _validate_usage_date(dataset_key, row_index, row["usage_date"])
        return self


def _validate_usage_date(dataset_key: str, row_index: int, value: Any) -> None:
    if isinstance(value, date):
        return
    if isinstance(value, str):
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError(
                f"Dataset {dataset_key}[{row_index}] has invalid usage_date: "
                "expected ISO YYYY-MM-DD"
            ) from None
        return
    raise ValueError(
        f"Dataset {dataset_key}[{row_index}] has invalid usage_date: "
        "expected ISO YYYY-MM-DD"
    )


class SourceAvailability(BaseModel):
    available: bool
    detail: str | None = None
    user_safe_message: str | None = None


class DashboardDatasetMetadata(BaseModel):
    data_mode: DashboardDataMode
    account_locator: str | None
    currency: str | None
    billing_through_date: date | None
    account_usage_through_date: date | None
    estimated_credit_price_usd: float
    storage_price_usd_per_tb_month: float
    unsupported_reason: UnsupportedReason | None = None
    organization_usage: SourceAvailability
    account_usage: SourceAvailability


class DashboardDatasetResponse(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run: DashboardRun
    summary: dict[str, Any]
    datasets: dict[str, list[dict[str, Any]]]
    metadata: DashboardDatasetMetadata | None = None

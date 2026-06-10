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
FORBIDDEN_RAW_DATASET_FIELDS = frozenset(
    {"private_key", "private_key_path", "query_id", "query_text", "sql_text"}
)


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
    def reject_raw_dataset_fields(self) -> Self:
        for dataset_key, rows in self.datasets.items():
            for row_index, row in enumerate(rows):
                forbidden_fields = sorted(
                    FORBIDDEN_RAW_DATASET_FIELDS.intersection(row)
                )
                if forbidden_fields:
                    fields = ", ".join(forbidden_fields)
                    raise ValueError(
                        f"Dataset {dataset_key}[{row_index}] contains "
                        f"forbidden raw fields: {fields}"
                    )
        return self


class DashboardDatasetResponse(BaseModel):
    run: DashboardRun
    summary: dict[str, Any]
    datasets: dict[str, list[dict[str, Any]]]

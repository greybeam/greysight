from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.models import DashboardDatasetMetadata, DashboardRun, SCHEMA_VERSION

DashboardRangeMode = Literal["relative", "custom"]
DashboardDataModeLabel = Literal["Billed", "Estimated", "Demo"]
SpendBasis = Literal["billed", "estimated"]
SectionStatus = Literal["pending", "ready", "unavailable"]


def _all_ready_section_statuses() -> dict[str, SectionStatus]:
    return {"overview": "ready", "warehouse": "ready", "storage": "ready"}


class DashboardViewRange(BaseModel):
    mode: DashboardRangeMode
    window_days: int | None
    start_date: date
    end_date: date


class DashboardProjectionRange(BaseModel):
    start_date: date
    end_date: date


class DollarPoint(BaseModel):
    date: str
    spend: float
    spend_label: str


class BalancePoint(BaseModel):
    date: str
    balance: float
    balance_label: str


class ServicePoint(BaseModel):
    date: str
    values: dict[str, float]


class RankedSpendRow(BaseModel):
    name: str
    spend: float
    spend_label: str
    credits: float | None


class RankedBarRow(RankedSpendRow):
    bar_width_percent: float


class WarehouseIdleBarRow(RankedSpendRow):
    idle_pct: float | None


class AIConsumptionPoint(BaseModel):
    date: str
    values: dict[str, float]


class AISpendSummaryViewModel(BaseModel):
    total: float
    total_label: str
    is_empty: bool


class AIDetailViewModel(BaseModel):
    daily_series: list[AIConsumptionPoint]
    consumption_type_names: list[str]
    ranked_consumption_types: list[RankedSpendRow]
    consumption_bars: list[RankedBarRow]
    is_empty: bool
    partial: bool
    skipped_branches: list[str]


class HeaderViewModel(BaseModel):
    data_mode_label: DashboardDataModeLabel
    account_locator: str | None
    currency: str
    through_date: str | None
    through_date_label: str | None
    freshness_label: str | None
    estimated_credit_price_label: str
    storage_price_label: str


class TotalSpendViewModel(BaseModel):
    basis: SpendBasis
    total: float
    total_label: str
    average_daily: float
    average_daily_label: str
    projected_monthly: float
    projected_monthly_label: str
    projection_basis_label: str
    daily_series: list[DollarPoint]
    top_driver: RankedSpendRow | None
    is_empty: bool


class CapacityBalanceViewModel(BaseModel):
    current_balance: float
    current_balance_label: str
    current_balance_date: str | None
    daily_series: list[BalancePoint]
    forecast_series: list[BalancePoint] = Field(default_factory=list)
    is_empty: bool


class WarehousePoint(BaseModel):
    date: str
    values: dict[str, float]


class WarehouseSpendViewModel(BaseModel):
    basis: SpendBasis
    total: float
    total_label: str
    daily_series: list[WarehousePoint]
    warehouse_names: list[str]
    ranked_warehouses: list[RankedSpendRow]
    ranked_users: list[RankedSpendRow]
    warehouse_bars: list[WarehouseIdleBarRow]
    user_bars: list[RankedBarRow]
    is_empty: bool


class StorageDatabaseRow(BaseModel):
    name: str
    bytes: float
    bytes_label: str
    monthly_spend: float
    monthly_spend_label: str
    period_spend: float
    period_spend_label: str


class StorageDatabasePoint(BaseModel):
    date: str
    values: dict[str, float]


class StorageSpendViewModel(BaseModel):
    basis: SpendBasis
    database_basis: SpendBasis
    total: float
    total_label: str
    daily_series: list[DollarPoint]
    database_names: list[str]
    database_daily_series: list[StorageDatabasePoint]
    databases: list[StorageDatabaseRow]
    database_bars: list[RankedBarRow]
    is_empty: bool


class ServiceSpendViewModel(BaseModel):
    basis: SpendBasis
    daily_series: list[ServicePoint]
    service_names: list[str]
    ranked_services: list[RankedSpendRow]
    service_bars: list[RankedBarRow]
    is_empty: bool


class WarehouseDetailRow(RankedSpendRow):
    credits_compute: float
    credits_total: float


class UserDetailRow(RankedSpendRow):
    warehouse_name: str


class DetailTablesViewModel(BaseModel):
    services: list[RankedSpendRow]
    warehouses: list[WarehouseDetailRow]
    users: list[UserDetailRow]
    storage: list[StorageDatabaseRow]


class UnsupportedViewModel(BaseModel):
    title: str
    detail: str


class DashboardViewResponse(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run: DashboardRun
    range: DashboardViewRange
    projection_range: DashboardProjectionRange
    header: HeaderViewModel
    unsupported: UnsupportedViewModel | None
    capacity_balance: CapacityBalanceViewModel
    total_spend: TotalSpendViewModel
    warehouse_spend: WarehouseSpendViewModel
    storage_spend: StorageSpendViewModel
    service_spend: ServiceSpendViewModel
    detail_tables: DetailTablesViewModel
    ai_spend_summary: AISpendSummaryViewModel = AISpendSummaryViewModel(
        total=0.0, total_label="$0.00", is_empty=True
    )
    section_statuses: dict[str, SectionStatus] = Field(
        default_factory=_all_ready_section_statuses
    )
    # Source-group availability metadata (data_mode, organization_usage /
    # account_usage with their curated detail/user_safe_message). The client
    # reads it to surface classified messages for sections whose source group
    # collapsed. Optional so legacy stored views without it stay valid.
    metadata: DashboardDatasetMetadata | None = None

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.models import DashboardRun, SCHEMA_VERSION

DashboardRangeMode = Literal["relative", "custom"]
DashboardDataModeLabel = Literal["Billed", "Estimated", "Demo"]
SpendBasis = Literal["billed", "estimated"]


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


class ComputeSpendViewModel(BaseModel):
    compute_basis: SpendBasis
    daily_series: list[DollarPoint]
    ranked_warehouses: list[RankedSpendRow]
    ranked_users: list[RankedSpendRow]
    warehouse_bars: list[RankedBarRow]
    user_bars: list[RankedBarRow]
    is_empty: bool


class StorageDatabaseRow(BaseModel):
    name: str
    bytes: float
    monthly_spend: float
    monthly_spend_label: str


class StorageSpendViewModel(BaseModel):
    basis: SpendBasis
    database_basis: SpendBasis
    daily_series: list[DollarPoint]
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
    total_spend: TotalSpendViewModel
    compute_spend: ComputeSpendViewModel
    storage_spend: StorageSpendViewModel
    service_spend: ServiceSpendViewModel
    detail_tables: DetailTablesViewModel

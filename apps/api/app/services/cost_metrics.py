from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel


class ServiceSpendDaily(BaseModel):
    usage_date: date
    service_type: str
    credits_used: float


class AccountSpendDaily(BaseModel):
    usage_date: date
    credits_used: float


class WarehouseSpendDaily(BaseModel):
    usage_date: date
    warehouse_name: str
    credits_used: float
    credits_used_compute: float = 0.0


class DatabaseStorageDaily(BaseModel):
    usage_date: date
    database_name: str | None = None
    average_database_bytes: float
    average_failsafe_bytes: float
    average_hybrid_table_storage_bytes: float | None = None


class DashboardSummary(BaseModel):
    total_credits: float
    average_daily_credits: float
    estimated_monthly_credits: float
    warehouse_count: int
    top_warehouse_name: str | None
    storage_bytes: float
    estimated_monthly_storage_cost_usd: float | None


def derive_account_spend_daily(
    service_spend_daily: list[ServiceSpendDaily | dict[str, Any]],
    *,
    current_usage_date: date | None = None,
) -> list[AccountSpendDaily]:
    credits_by_date: dict[date, float] = defaultdict(float)
    for row in service_spend_daily:
        service_row = ServiceSpendDaily.model_validate(row)
        if service_row.usage_date == current_usage_date:
            continue
        credits_by_date[service_row.usage_date] += service_row.credits_used

    return [
        AccountSpendDaily(
            usage_date=usage_date, credits_used=credits_by_date[usage_date]
        )
        for usage_date in sorted(credits_by_date)
    ]


def build_dashboard_summary(
    *,
    account_spend_daily: list[AccountSpendDaily | dict[str, Any]],
    warehouse_spend_daily: list[WarehouseSpendDaily | dict[str, Any]],
    database_storage_daily: list[DatabaseStorageDaily | dict[str, Any]],
    current_usage_date: date,
    window_days: int,
    storage_price_usd_per_tb_month: float | None = None,
) -> DashboardSummary:
    complete_window_start, complete_window_end = _complete_window_bounds(
        current_usage_date=current_usage_date,
        window_days=window_days,
    )
    account_rows = [
        AccountSpendDaily.model_validate(row) for row in account_spend_daily
    ]
    complete_account_rows = _filter_complete_window(
        account_rows,
        start_date=complete_window_start,
        end_date=complete_window_end,
    )
    total_credits = sum(row.credits_used for row in complete_account_rows)
    average_daily_credits = total_credits / window_days

    warehouse_rows = [
        WarehouseSpendDaily.model_validate(row) for row in warehouse_spend_daily
    ]
    complete_warehouse_rows = _filter_complete_window(
        warehouse_rows,
        start_date=complete_window_start,
        end_date=complete_window_end,
    )
    warehouse_names = {row.warehouse_name for row in complete_warehouse_rows}
    credits_by_warehouse: dict[str, float] = defaultdict(float)
    for row in complete_warehouse_rows:
        credits_by_warehouse[row.warehouse_name] += row.credits_used
    top_warehouse_name = _top_warehouse_name(credits_by_warehouse)

    storage_bytes = _latest_complete_storage_bytes(
        database_storage_daily,
        start_date=complete_window_start,
        end_date=complete_window_end,
    )
    estimated_monthly_storage_cost_usd = None
    if storage_price_usd_per_tb_month is not None:
        estimated_monthly_storage_cost_usd = (
            storage_bytes / 1_000_000_000_000
        ) * storage_price_usd_per_tb_month

    return DashboardSummary(
        total_credits=total_credits,
        average_daily_credits=average_daily_credits,
        estimated_monthly_credits=average_daily_credits * 30,
        warehouse_count=len(warehouse_names),
        top_warehouse_name=top_warehouse_name,
        storage_bytes=storage_bytes,
        estimated_monthly_storage_cost_usd=estimated_monthly_storage_cost_usd,
    )


def _complete_window_bounds(
    *,
    current_usage_date: date,
    window_days: int,
) -> tuple[date, date]:
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    return current_usage_date - timedelta(days=window_days), current_usage_date


def _top_warehouse_name(credits_by_warehouse: dict[str, float]) -> str | None:
    if not credits_by_warehouse:
        return None
    return max(
        credits_by_warehouse,
        key=lambda warehouse_name: (
            credits_by_warehouse[warehouse_name],
            warehouse_name,
        ),
    )


def _filter_complete_window[
    T: AccountSpendDaily | WarehouseSpendDaily | DatabaseStorageDaily,
](
    rows: list[T],
    *,
    start_date: date,
    end_date: date,
) -> list[T]:
    return [row for row in rows if start_date <= row.usage_date < end_date]


def _latest_complete_storage_bytes(
    database_storage_daily: list[DatabaseStorageDaily | dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
) -> float:
    storage_rows = [
        DatabaseStorageDaily.model_validate(row) for row in database_storage_daily
    ]
    complete_rows = _filter_complete_window(
        storage_rows,
        start_date=start_date,
        end_date=end_date,
    )
    if not complete_rows:
        return 0.0

    latest_usage_date = max(row.usage_date for row in complete_rows)
    return sum(
        row.average_database_bytes
        + row.average_failsafe_bytes
        + (row.average_hybrid_table_storage_bytes or 0.0)
        for row in complete_rows
        if row.usage_date == latest_usage_date
    )

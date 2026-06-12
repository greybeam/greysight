from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel

from app.models import SCHEMA_VERSION, DashboardDatasetMetadata, SourceAvailability
from app.services.cost_metrics import (
    DashboardSummary,
    build_dashboard_summary,
    derive_account_spend_daily,
)
from app.services.dashboard_datasets import build_top_warehouses_table

DEMO_FETCH_DAYS = 100
DEMO_TODAY = date(2026, 6, 10)
DEMO_BILLING_THROUGH = date(2026, 6, 8)
DEMO_ACCOUNT_USAGE_THROUGH = DEMO_BILLING_THROUGH
DEMO_ACCOUNT_LOCATOR = "DEMO123"
DEMO_CREDIT_RATE_USD = 2.25
DEMO_STORAGE_RATE_USD = 25.0
DEMO_CAPACITY_START_USD = 75_000.0

_DEMO_SERVICES = (
    ("WAREHOUSE_METERING", "COMPUTE", 38.0),
    ("CLOUD_SERVICES", "COMPUTE", 4.0),
    ("AUTO_CLUSTERING", "COMPUTE", 1.5),
)
_DEMO_WAREHOUSES = (
    ("BI_WH", 0.50),
    ("ETL_WH", 0.35),
    ("ADHOC_WH", 0.15),
)
_DEMO_USERS = (
    ("ANALYST_A", "BI_WH", 0.34),
    ("ANALYST_B", "ADHOC_WH", 0.22),
    ("DATA_ENGINEER", "ETL_WH", 0.30),
    ("AIRFLOW_SVC", "ETL_WH", 0.14),
)
_DEMO_DATABASES = (
    ("RAW", 3.6),
    ("ANALYTICS", 2.3),
    ("APP", 1.1),
)


class DemoRun(BaseModel):
    id: str
    status: str
    source: str
    window_days: int
    started_at: datetime
    completed_at: datetime


class DashboardDatasetPayload(BaseModel):
    schema_version: int
    run: DemoRun
    summary: DashboardSummary
    datasets: dict[str, list[dict[str, Any]]]
    metadata: DashboardDatasetMetadata


def build_demo_dashboard_dataset() -> DashboardDatasetPayload:
    usage_dates = [
        DEMO_BILLING_THROUGH - timedelta(days=day_offset)
        for day_offset in reversed(range(DEMO_FETCH_DAYS))
    ]
    service_spend_daily = _build_service_spend_daily(usage_dates)
    warehouse_spend_daily = _build_warehouse_spend_daily(usage_dates)
    query_compute_by_user_daily = _build_query_compute_by_user_daily(usage_dates)
    database_storage_daily = _build_database_storage_daily(usage_dates)
    org_spend_daily = _build_org_spend_daily(service_spend_daily)
    rate_sheet_daily = _build_rate_sheet_daily(usage_dates)
    capacity_balance_daily = _build_capacity_balance_daily(usage_dates, org_spend_daily)
    account_spend_daily = derive_account_spend_daily(service_spend_daily)
    top_warehouses_table = build_top_warehouses_table(warehouse_spend_daily)
    datasets = {
        "account_spend_daily": _json_ready_rows(account_spend_daily),
        "warehouse_spend_daily": _json_ready_rows(warehouse_spend_daily),
        "service_spend_daily": _json_ready_rows(service_spend_daily),
        "query_compute_by_user_daily": _json_ready_rows(query_compute_by_user_daily),
        "database_storage_daily": _json_ready_rows(database_storage_daily),
        "top_warehouses_table": top_warehouses_table,
        "org_spend_daily": _json_ready_rows(org_spend_daily),
        "rate_sheet_daily": _json_ready_rows(rate_sheet_daily),
        "capacity_balance_daily": _json_ready_rows(capacity_balance_daily),
        "current_account": [{"account_locator": DEMO_ACCOUNT_LOCATOR}],
    }
    summary = build_dashboard_summary(
        account_spend_daily=account_spend_daily,
        warehouse_spend_daily=warehouse_spend_daily,
        database_storage_daily=database_storage_daily,
        current_usage_date=DEMO_BILLING_THROUGH,
        window_days=DEMO_FETCH_DAYS,
        storage_price_usd_per_tb_month=DEMO_STORAGE_RATE_USD,
    )
    metadata = DashboardDatasetMetadata(
        data_mode="demo",
        account_locator=DEMO_ACCOUNT_LOCATOR,
        currency="USD",
        billing_through_date=DEMO_BILLING_THROUGH,
        account_usage_through_date=DEMO_ACCOUNT_USAGE_THROUGH,
        estimated_credit_price_usd=DEMO_CREDIT_RATE_USD,
        storage_price_usd_per_tb_month=DEMO_STORAGE_RATE_USD,
        unsupported_reason=None,
        organization_usage=SourceAvailability(available=True),
        account_usage=SourceAvailability(available=True),
    )
    completed_at = datetime.combine(
        DEMO_TODAY, datetime.min.time(), tzinfo=timezone.utc
    )

    return DashboardDatasetPayload(
        schema_version=SCHEMA_VERSION,
        run=DemoRun(
            id="demo-run",
            status="completed",
            source="demo",
            window_days=DEMO_FETCH_DAYS,
            started_at=completed_at,
            completed_at=completed_at,
        ),
        summary=summary,
        datasets=datasets,
        metadata=metadata,
    )


def _build_service_spend_daily(usage_dates: list[date]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, usage_date in enumerate(usage_dates):
        multiplier = _daily_multiplier(index)
        for service_type, _rating_type, base_credits in _DEMO_SERVICES:
            rows.append(
                {
                    "usage_date": usage_date,
                    "service_type": service_type,
                    "credits_used": round(base_credits * multiplier, 3),
                }
            )
    return rows


def _build_warehouse_spend_daily(usage_dates: list[date]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, usage_date in enumerate(usage_dates):
        total_compute = 38.0 * _daily_multiplier(index)
        for warehouse_name, share in _DEMO_WAREHOUSES:
            compute_credits = round(total_compute * share, 3)
            rows.append(
                {
                    "usage_date": usage_date,
                    "warehouse_name": warehouse_name,
                    "credits_used": round(compute_credits * 1.08, 3),
                    "credits_used_compute": compute_credits,
                }
            )
    return rows


def _build_query_compute_by_user_daily(usage_dates: list[date]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, usage_date in enumerate(usage_dates):
        total_compute = 38.0 * _daily_multiplier(index)
        for user_name, warehouse_name, share in _DEMO_USERS:
            rows.append(
                {
                    "usage_date": usage_date,
                    "user_name": user_name,
                    "warehouse_name": warehouse_name,
                    "credits_attributed_compute": round(total_compute * share, 3),
                }
            )
    return rows


def _build_database_storage_daily(usage_dates: list[date]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, usage_date in enumerate(usage_dates):
        growth_factor = 1 + (index / 500)
        for database_name, base_tb in _DEMO_DATABASES:
            rows.append(
                {
                    "usage_date": usage_date,
                    "database_name": database_name,
                    "average_database_bytes": round(
                        base_tb * growth_factor * 1_000_000_000_000
                    ),
                    "average_failsafe_bytes": round(
                        base_tb * 0.08 * growth_factor * 1_000_000_000_000
                    ),
                }
            )
    return rows


def _build_org_spend_daily(
    service_spend_daily: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in service_spend_daily:
        service_type = str(row["service_type"])
        rating_type = "COMPUTE" if service_type != "STORAGE" else "STORAGE"
        rows.append(
            {
                "usage_date": row["usage_date"],
                "service_type": service_type,
                "rating_type": rating_type,
                "billing_type": "CONSUMPTION",
                "is_adjustment": False,
                "currency": "USD",
                "spend": round(float(row["credits_used"]) * DEMO_CREDIT_RATE_USD, 2),
            }
        )
    return rows


def _build_rate_sheet_daily(usage_dates: list[date]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for usage_date in usage_dates:
        for service_type, rating_type, _base_credits in _DEMO_SERVICES:
            rows.append(
                {
                    "usage_date": usage_date,
                    "service_type": service_type,
                    "rating_type": rating_type,
                    "currency": "USD",
                    "effective_rate": DEMO_CREDIT_RATE_USD,
                }
            )
    return rows


def _build_capacity_balance_daily(
    usage_dates: list[date],
    org_spend_daily: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    spend_by_date = {usage_date: 0.0 for usage_date in usage_dates}
    for row in org_spend_daily:
        usage_date = row["usage_date"]
        assert isinstance(usage_date, date)
        spend_by_date[usage_date] += float(row["spend"])

    balance = DEMO_CAPACITY_START_USD
    rows: list[dict[str, Any]] = []
    for usage_date in usage_dates:
        balance -= spend_by_date[usage_date]
        rows.append(
            {
                "usage_date": usage_date,
                "currency": "USD",
                "balance": round(balance, 2),
            }
        )
    return rows


def _daily_multiplier(index: int) -> float:
    weekday_shape = (index % 7) * 0.025
    month_shape = (index % 30) * 0.004
    return round(0.9 + weekday_shape + month_shape, 4)


def _json_ready_rows(rows: list[dict[str, Any] | BaseModel]) -> list[dict[str, Any]]:
    return [
        {
            key: value.isoformat() if isinstance(value, date) else value
            for key, value in (
                row.model_dump().items() if isinstance(row, BaseModel) else row.items()
            )
        }
        for row in rows
    ]

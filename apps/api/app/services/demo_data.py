from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel

from app.services.cost_metrics import (
    DashboardSummary,
    build_dashboard_summary,
    derive_account_spend_daily,
)


class DemoRun(BaseModel):
    id: str
    status: str
    source: str
    window_days: int
    started_at: datetime
    completed_at: datetime
    error: str | None = None


class DashboardDatasetPayload(BaseModel):
    run: DemoRun
    summary: DashboardSummary
    datasets: dict[str, list[dict[str, Any]]]


def build_demo_dashboard_dataset() -> DashboardDatasetPayload:
    current_usage_date = date(2026, 6, 8)
    service_spend_daily = [
        {
            "usage_date": date(2026, 6, 5),
            "service_type": "WAREHOUSE_METERING",
            "credits_used": 37.5,
        },
        {
            "usage_date": date(2026, 6, 5),
            "service_type": "CLOUD_SERVICES",
            "credits_used": 4.0,
        },
        {
            "usage_date": date(2026, 6, 6),
            "service_type": "WAREHOUSE_METERING",
            "credits_used": 39.0,
        },
        {
            "usage_date": date(2026, 6, 6),
            "service_type": "CLOUD_SERVICES",
            "credits_used": 4.5,
        },
        {
            "usage_date": date(2026, 6, 7),
            "service_type": "WAREHOUSE_METERING",
            "credits_used": 42.0,
        },
        {
            "usage_date": date(2026, 6, 7),
            "service_type": "CLOUD_SERVICES",
            "credits_used": 5.0,
        },
    ]
    warehouse_spend_daily = [
        {
            "usage_date": date(2026, 6, 5),
            "warehouse_name": "BI_WH",
            "credits_used": 18.0,
        },
        {
            "usage_date": date(2026, 6, 5),
            "warehouse_name": "ETL_WH",
            "credits_used": 14.5,
        },
        {
            "usage_date": date(2026, 6, 5),
            "warehouse_name": "ADHOC_WH",
            "credits_used": 5.0,
        },
        {
            "usage_date": date(2026, 6, 6),
            "warehouse_name": "BI_WH",
            "credits_used": 19.0,
        },
        {
            "usage_date": date(2026, 6, 6),
            "warehouse_name": "ETL_WH",
            "credits_used": 15.5,
        },
        {
            "usage_date": date(2026, 6, 6),
            "warehouse_name": "ADHOC_WH",
            "credits_used": 4.5,
        },
        {
            "usage_date": date(2026, 6, 7),
            "warehouse_name": "BI_WH",
            "credits_used": 21.0,
        },
        {
            "usage_date": date(2026, 6, 7),
            "warehouse_name": "ETL_WH",
            "credits_used": 16.0,
        },
        {
            "usage_date": date(2026, 6, 7),
            "warehouse_name": "ADHOC_WH",
            "credits_used": 5.0,
        },
    ]
    database_storage_daily = [
        {
            "usage_date": date(2026, 6, 5),
            "database_name": "RAW",
            "average_database_bytes": 3_500_000_000_000,
            "average_failsafe_bytes": 400_000_000_000,
        },
        {
            "usage_date": date(2026, 6, 5),
            "database_name": "ANALYTICS",
            "average_database_bytes": 2_200_000_000_000,
            "average_failsafe_bytes": 200_000_000_000,
        },
        {
            "usage_date": date(2026, 6, 7),
            "database_name": "RAW",
            "average_database_bytes": 3_700_000_000_000,
            "average_failsafe_bytes": 450_000_000_000,
        },
        {
            "usage_date": date(2026, 6, 7),
            "database_name": "ANALYTICS",
            "average_database_bytes": 2_300_000_000_000,
            "average_failsafe_bytes": 250_000_000_000,
        },
    ]

    account_spend_daily = derive_account_spend_daily(
        service_spend_daily,
        current_usage_date=current_usage_date,
    )
    summary = build_dashboard_summary(
        account_spend_daily=account_spend_daily,
        warehouse_spend_daily=warehouse_spend_daily,
        database_storage_daily=database_storage_daily,
        complete_day_count=len(account_spend_daily),
        current_usage_date=current_usage_date,
        storage_price_usd_per_tb_month=None,
    )

    datasets = {
        "account_spend_daily": _dump_rows(account_spend_daily),
        "warehouse_spend_daily": _dump_rows(warehouse_spend_daily),
        "service_spend_daily": _dump_rows(service_spend_daily),
        "query_compute_by_user_daily": _dump_rows(_user_compute_rows()),
        "database_storage_daily": _dump_rows(database_storage_daily),
        "top_warehouses_table": _top_warehouse_rows(warehouse_spend_daily),
    }

    return DashboardDatasetPayload(
        run=DemoRun(
            id="demo-run",
            status="completed",
            source="demo",
            window_days=30,
            started_at=datetime(2026, 6, 8, 0, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 6, 8, 0, 0, 1, tzinfo=timezone.utc),
        ),
        summary=summary,
        datasets=datasets,
    )


def _user_compute_rows() -> list[dict[str, Any]]:
    return [
        {
            "usage_date": date(2026, 6, 5),
            "user_name": "ANALYST_A",
            "warehouse_name": "BI_WH",
            "credits_used": 12.0,
        },
        {
            "usage_date": date(2026, 6, 5),
            "user_name": "ANALYST_B",
            "warehouse_name": "ADHOC_WH",
            "credits_used": 8.5,
        },
        {
            "usage_date": date(2026, 6, 6),
            "user_name": "ANALYST_A",
            "warehouse_name": "BI_WH",
            "credits_used": 13.0,
        },
        {
            "usage_date": date(2026, 6, 6),
            "user_name": "DATA_ENGINEER",
            "warehouse_name": "ETL_WH",
            "credits_used": 10.5,
        },
        {
            "usage_date": date(2026, 6, 7),
            "user_name": "DATA_ENGINEER",
            "warehouse_name": "ETL_WH",
            "credits_used": 14.0,
        },
        {
            "usage_date": date(2026, 6, 7),
            "user_name": "ANALYST_B",
            "warehouse_name": "ADHOC_WH",
            "credits_used": 9.0,
        },
    ]


def _top_warehouse_rows(
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
            credits_by_warehouse.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def _dump_rows(rows: list[Any]) -> list[dict[str, Any]]:
    dumped_rows: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, BaseModel):
            dumped_rows.append(row.model_dump(mode="json"))
        else:
            dumped_rows.append(
                {
                    key: value.isoformat() if isinstance(value, date) else value
                    for key, value in row.items()
                }
            )
    return dumped_rows

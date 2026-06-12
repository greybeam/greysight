from datetime import date

import pytest

from app.services.cost_metrics import (
    AccountSpendDaily,
    build_dashboard_summary,
    DatabaseStorageDaily,
    derive_account_spend_daily,
    WarehouseSpendDaily,
)


def test_derive_account_spend_daily_rolls_up_services_and_excludes_current_date() -> (
    None
):
    service_rows = [
        {
            "usage_date": date(2026, 6, 5),
            "service_type": "WAREHOUSE_METERING",
            "credits_used": 10.0,
        },
        {
            "usage_date": date(2026, 6, 5),
            "service_type": "CLOUD_SERVICES",
            "credits_used": 5.0,
        },
        {
            "usage_date": date(2026, 6, 6),
            "service_type": "WAREHOUSE_METERING",
            "credits_used": 20.0,
        },
        {
            "usage_date": date(2026, 6, 7),
            "service_type": "WAREHOUSE_METERING",
            "credits_used": 999.0,
        },
    ]

    account_rows = derive_account_spend_daily(
        service_rows,
        current_usage_date=date(2026, 6, 7),
    )

    assert account_rows == [
        AccountSpendDaily(usage_date=date(2026, 6, 5), credits_used=15.0),
        AccountSpendDaily(usage_date=date(2026, 6, 6), credits_used=20.0),
    ]


def test_build_dashboard_summary_applies_metric_rules() -> None:
    summary = build_dashboard_summary(
        account_spend_daily=[
            {"usage_date": date(2026, 6, 5), "credits_used": 15.0},
            {"usage_date": date(2026, 6, 6), "credits_used": 20.0},
            {"usage_date": date(2026, 6, 7), "credits_used": 999.0},
        ],
        warehouse_spend_daily=[
            {
                "usage_date": date(2026, 6, 5),
                "warehouse_name": "BI_WH",
                "credits_used": 2.0,
            },
            {
                "usage_date": date(2026, 6, 6),
                "warehouse_name": "BI_WH",
                "credits_used": 3.0,
            },
            {
                "usage_date": date(2026, 6, 6),
                "warehouse_name": "ETL_WH",
                "credits_used": 8.0,
            },
            {
                "usage_date": date(2026, 6, 7),
                "warehouse_name": "INCOMPLETE_WH",
                "credits_used": 1000.0,
            },
        ],
        database_storage_daily=[
            {
                "usage_date": date(2026, 6, 5),
                "database_name": "RAW",
                "average_database_bytes": 1_000_000_000_000,
                "average_failsafe_bytes": 0,
            },
            {
                "usage_date": date(2026, 6, 6),
                "database_name": "RAW",
                "average_database_bytes": 2_000_000_000_000,
                "average_failsafe_bytes": 500_000_000_000,
            },
            {
                "usage_date": date(2026, 6, 7),
                "database_name": "RAW",
                "average_database_bytes": 99_000_000_000_000,
                "average_failsafe_bytes": 0,
            },
        ],
        current_usage_date=date(2026, 6, 7),
        window_days=2,
        storage_price_usd_per_tb_month=23.0,
    )

    assert summary.total_credits == 35.0
    assert summary.average_daily_credits == 17.5
    assert summary.estimated_monthly_credits == 525.0
    assert summary.warehouse_count == 2
    assert summary.top_warehouse_name == "ETL_WH"
    assert summary.storage_bytes == 2_500_000_000_000
    assert summary.estimated_monthly_storage_cost_usd == 57.5


def test_storage_cost_is_null_without_a_price() -> None:
    summary = build_dashboard_summary(
        account_spend_daily=[],
        warehouse_spend_daily=[],
        database_storage_daily=[
            {
                "usage_date": date(2026, 6, 5),
                "database_name": "RAW",
                "average_database_bytes": 1_000_000_000_000,
                "average_failsafe_bytes": 0,
            },
        ],
        current_usage_date=date(2026, 6, 6),
        window_days=1,
        storage_price_usd_per_tb_month=None,
    )

    assert summary.storage_bytes == 1_000_000_000_000
    assert summary.estimated_monthly_storage_cost_usd is None


def test_derive_account_spend_daily_without_current_date_rolls_up_all_dates() -> None:
    service_rows = [
        {
            "usage_date": date(2026, 6, 5),
            "service_type": "WAREHOUSE_METERING",
            "credits_used": 10.0,
        },
        {
            "usage_date": date(2026, 6, 6),
            "service_type": "CLOUD_SERVICES",
            "credits_used": 3.5,
        },
        {
            "usage_date": date(2026, 6, 7),
            "service_type": "WAREHOUSE_METERING",
            "credits_used": 7.0,
        },
    ]

    account_rows = derive_account_spend_daily(
        service_rows,
        current_usage_date=None,
    )

    assert account_rows == [
        AccountSpendDaily(usage_date=date(2026, 6, 5), credits_used=10.0),
        AccountSpendDaily(usage_date=date(2026, 6, 6), credits_used=3.5),
        AccountSpendDaily(usage_date=date(2026, 6, 7), credits_used=7.0),
    ]


def test_database_storage_daily_accepts_fractional_average_bytes() -> None:
    row = DatabaseStorageDaily(
        usage_date=date(2026, 6, 6),
        database_name="RAW",
        average_database_bytes=1.5,
        average_failsafe_bytes=0.25,
    )

    assert row.average_database_bytes == 1.5
    assert row.average_failsafe_bytes == 0.25


def test_warehouse_spend_daily_accepts_credits_used_compute() -> None:
    row = WarehouseSpendDaily.model_validate(
        {
            "usage_date": date(2026, 6, 5),
            "warehouse_name": "BI_WH",
            "credits_used": 10.0,
            "credits_used_compute": 9.2,
        }
    )

    assert row.credits_used_compute == 9.2


def test_warehouse_spend_daily_defaults_credits_used_compute_to_zero() -> None:
    row = WarehouseSpendDaily.model_validate(
        {
            "usage_date": date(2026, 6, 5),
            "warehouse_name": "BI_WH",
            "credits_used": 10.0,
        }
    )

    assert row.credits_used_compute == 0.0


def test_build_dashboard_summary_uses_float_storage_bytes_and_cost() -> None:
    summary = build_dashboard_summary(
        account_spend_daily=[
            {"usage_date": date(2026, 6, 6), "credits_used": 12.0},
        ],
        warehouse_spend_daily=[
            {
                "usage_date": date(2026, 6, 6),
                "warehouse_name": "BI_WH",
                "credits_used": 12.0,
            },
        ],
        database_storage_daily=[
            {
                "usage_date": date(2026, 6, 6),
                "database_name": "RAW",
                "average_database_bytes": 1_500_000_000_000.5,
                "average_failsafe_bytes": 250_000_000_000.25,
            }
        ],
        current_usage_date=date(2026, 6, 7),
        window_days=1,
        storage_price_usd_per_tb_month=23.0,
    )

    assert summary.storage_bytes == 1_750_000_000_000.75
    assert summary.estimated_monthly_storage_cost_usd == pytest.approx(40.25)


def test_build_dashboard_summary_averages_over_sparse_calendar_window() -> None:
    summary = build_dashboard_summary(
        account_spend_daily=[
            {"usage_date": date(2026, 6, 5), "credits_used": 30.0},
        ],
        warehouse_spend_daily=[
            {
                "usage_date": date(2026, 6, 5),
                "warehouse_name": "BI_WH",
                "credits_used": 30.0,
            },
        ],
        database_storage_daily=[],
        current_usage_date=date(2026, 6, 8),
        window_days=3,
        storage_price_usd_per_tb_month=23.0,
    )

    assert summary.total_credits == 30.0
    assert summary.average_daily_credits == 10.0
    assert summary.estimated_monthly_credits == 300.0


def test_build_dashboard_summary_excludes_rows_outside_trailing_complete_window() -> (
    None
):
    summary = build_dashboard_summary(
        account_spend_daily=[
            {"usage_date": date(2026, 6, 4), "credits_used": 999.0},
            {"usage_date": date(2026, 6, 5), "credits_used": 30.0},
        ],
        warehouse_spend_daily=[
            {
                "usage_date": date(2026, 6, 4),
                "warehouse_name": "OLD_WH",
                "credits_used": 999.0,
            },
            {
                "usage_date": date(2026, 6, 5),
                "warehouse_name": "BI_WH",
                "credits_used": 30.0,
            },
        ],
        database_storage_daily=[],
        current_usage_date=date(2026, 6, 8),
        window_days=3,
        storage_price_usd_per_tb_month=23.0,
    )

    assert summary.total_credits == 30.0
    assert summary.average_daily_credits == 10.0
    assert summary.estimated_monthly_credits == 300.0
    assert summary.warehouse_count == 1
    assert summary.top_warehouse_name == "BI_WH"

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.config import Settings
from app.models import SAFE_DATASET_ROW_FIELDS
from app.services.dashboard_datasets import (
    FETCH_WINDOW_DAYS,
    DashboardSourcesUnavailableError,
    build_snowflake_dashboard_data,
    build_top_warehouses_table,
)
from app.services.dataset_bounds import TOP_USER_COUNT
from app.services.snowflake_client import (
    SnowflakeObjectUnavailableError,
    SnowflakeQueryError,
)


def _account_rows() -> dict[str, list[dict[str, Any]]]:
    return {
        "warehouse_spend_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "warehouse_name": "LOAD_WH",
                "credits_used": 8.0,
                "credits_used_compute": 7.5,
            }
        ],
        "service_spend_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "service_type": "WAREHOUSE_METERING",
                "credits_used": 8.0,
            },
            {
                "usage_date": date(2026, 6, 5),
                "service_type": "CLOUD_SERVICES",
                "credits_used": 2.0,
            },
        ],
        "query_compute_by_user_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "user_name": "ANALYST",
                "warehouse_name": "LOAD_WH",
                "credits_attributed_compute": 8.0,
            }
        ],
        "database_storage_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "database_name": "RAW",
                "average_database_bytes": 1_000_000_000_000,
                "average_failsafe_bytes": 0,
            }
        ],
    }


def _org_rows() -> dict[str, list[dict[str, Any]]]:
    return {
        "org_spend_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "service_type": "WAREHOUSE_METERING",
                "rating_type": "COMPUTE",
                "billing_type": "CONSUMPTION",
                "is_adjustment": False,
                "currency": "USD",
                "spend": 24.0,
            }
        ],
        "rate_sheet_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "service_type": "WAREHOUSE_METERING",
                "rating_type": "COMPUTE",
                "currency": "USD",
                "effective_rate": 3.0,
            }
        ],
        "capacity_balance_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "currency": "USD",
                "balance": 15_000.0,
            }
        ],
    }


def _fake_execute(
    *,
    org_fails: bool = False,
    capacity_fails: bool = False,
    account_fails: bool = False,
    org_rows: dict[str, list[dict[str, Any]]] | None = None,
    account_rows: dict[str, list[dict[str, Any]]] | None = None,
    account_locator: str = "TU24199",
):
    org_datasets = org_rows or _org_rows()
    account_datasets = account_rows or _account_rows()

    def execute(sql: str, bind_params: dict[str, Any]) -> list[dict[str, Any]]:
        lowered = sql.lower()
        if "current_account()" in lowered:
            assert bind_params == {}
            return [{"account_locator": account_locator}]
        if "remaining_balance_daily" in lowered:
            assert bind_params == {
                "window_days": FETCH_WINDOW_DAYS,
                "account_locator": account_locator,
            }
            if capacity_fails:
                raise SnowflakeQueryError("capacity balance unavailable")
            return org_datasets["capacity_balance_daily"]
        if "organization_usage" in lowered:
            assert bind_params == {
                "window_days": FETCH_WINDOW_DAYS,
                "account_locator": account_locator,
            }
            if org_fails:
                raise SnowflakeQueryError("org usage unavailable")
            if "usage_in_currency_daily" in lowered:
                return org_datasets["org_spend_daily"]
            if "rate_sheet_daily" in lowered:
                return org_datasets["rate_sheet_daily"]
        assert bind_params == {"window_days": FETCH_WINDOW_DAYS}
        if account_fails:
            raise SnowflakeQueryError("account usage unavailable")
        for dataset_key, rows in account_datasets.items():
            if _known_account_sql_matches(dataset_key, lowered):
                return rows
        raise AssertionError(f"unexpected SQL: {sql}")

    return execute


def test_builds_billed_dashboard_data_with_metadata_and_bounded_rows() -> None:
    users = [
        {
            "usage_date": date(2026, 6, 5),
            "user_name": f"USER_{index:03}",
            "warehouse_name": "LOAD_WH",
            "credits_attributed_compute": 1.0,
        }
        for index in range(TOP_USER_COUNT + 5)
    ]
    account_rows = _account_rows()
    account_rows["query_compute_by_user_daily"] = users

    data = build_snowflake_dashboard_data(
        Settings(), execute=_fake_execute(account_rows=account_rows)
    )

    assert set(data.datasets) == set(SAFE_DATASET_ROW_FIELDS)
    assert data.metadata.data_mode == "billed"
    assert data.metadata.account_locator == "TU24199"
    assert data.metadata.currency == "USD"
    assert data.metadata.billing_through_date == date(2026, 6, 5)
    assert data.metadata.account_usage_through_date == date(2026, 6, 5)
    assert data.metadata.organization_usage.available is True
    assert data.metadata.account_usage.available is True
    assert data.datasets["account_spend_daily"] == [
        {"usage_date": "2026-06-05", "credits_used": 10.0}
    ]
    assert data.datasets["capacity_balance_daily"] == [
        {"usage_date": "2026-06-05", "currency": "USD", "balance": 15000}
    ]
    assert data.datasets["top_warehouses_table"] == [
        {"warehouse_name": "LOAD_WH", "credits_used": 8.0}
    ]
    assert len(data.datasets["query_compute_by_user_daily"]) == TOP_USER_COUNT + 1


def test_capacity_balance_failure_does_not_drop_billed_org_usage() -> None:
    data = build_snowflake_dashboard_data(
        Settings(), execute=_fake_execute(capacity_fails=True)
    )

    assert data.metadata.data_mode == "billed"
    assert data.datasets["org_spend_daily"]
    assert data.datasets["rate_sheet_daily"]
    assert data.datasets["capacity_balance_daily"] == []


def test_normalizes_decimal_snowflake_numbers_for_json_payloads() -> None:
    account_rows = _account_rows()
    account_rows["warehouse_spend_daily"][0]["credits_used"] = Decimal("8.250000000")
    account_rows["warehouse_spend_daily"][0]["credits_used_compute"] = Decimal(
        "7.500000000"
    )
    account_rows["service_spend_daily"][0]["credits_used"] = Decimal("8.000000000")
    account_rows["service_spend_daily"][1]["credits_used"] = Decimal("2.000000000")
    account_rows["query_compute_by_user_daily"][0]["credits_attributed_compute"] = (
        Decimal("6.125000000")
    )
    account_rows["database_storage_daily"][0]["average_database_bytes"] = Decimal(
        "1000000000000"
    )
    org_rows = _org_rows()
    org_rows["org_spend_daily"][0]["spend"] = Decimal("24.500000000")
    org_rows["rate_sheet_daily"][0]["effective_rate"] = Decimal("3.250000000")

    data = build_snowflake_dashboard_data(
        Settings(),
        execute=_fake_execute(account_rows=account_rows, org_rows=org_rows),
    )

    assert data.datasets["warehouse_spend_daily"][0]["credits_used"] == 8.25
    assert isinstance(data.datasets["warehouse_spend_daily"][0]["credits_used"], float)
    assert data.datasets["warehouse_spend_daily"][0]["credits_used_compute"] == 7.5
    assert isinstance(
        data.datasets["warehouse_spend_daily"][0]["credits_used_compute"], float
    )
    assert (
        data.datasets["query_compute_by_user_daily"][0]["credits_attributed_compute"]
        == 6.125
    )
    assert isinstance(
        data.datasets["query_compute_by_user_daily"][0]["credits_attributed_compute"],
        float,
    )
    assert (
        data.datasets["database_storage_daily"][0]["average_database_bytes"]
        == 1_000_000_000_000
    )
    assert isinstance(
        data.datasets["database_storage_daily"][0]["average_database_bytes"], int
    )
    assert data.datasets["org_spend_daily"][0]["spend"] == 24.5
    assert isinstance(data.datasets["org_spend_daily"][0]["spend"], float)
    assert data.datasets["rate_sheet_daily"][0]["effective_rate"] == 3.25
    assert isinstance(data.datasets["rate_sheet_daily"][0]["effective_rate"], float)


def test_falls_back_to_estimated_mode_when_org_usage_is_unavailable() -> None:
    data = build_snowflake_dashboard_data(
        Settings(), execute=_fake_execute(org_fails=True)
    )

    assert data.metadata.data_mode == "estimated"
    assert data.metadata.organization_usage.available is False
    assert data.metadata.account_usage.available is True
    assert data.metadata.billing_through_date is None
    assert data.metadata.currency == "USD"
    assert data.datasets["org_spend_daily"] == []
    assert data.datasets["rate_sheet_daily"] == []


def test_object_unavailable_in_main_run_group_falls_back_gracefully() -> None:
    # Regression: an org-usage view that is missing/unauthorized raises
    # SnowflakeObjectUnavailableError. Because it is a SnowflakeQueryError, the
    # main-run source-group fallback must catch it and degrade to estimated data
    # rather than failing the whole run with DashboardSourcesUnavailableError.
    org_datasets = _org_rows()
    account_datasets = _account_rows()

    def execute(sql: str, bind_params: dict[str, Any]) -> list[dict[str, Any]]:
        lowered = sql.lower()
        if "current_account()" in lowered:
            return [{"account_locator": "TU24199"}]
        if "remaining_balance_daily" in lowered:
            raise SnowflakeObjectUnavailableError("capacity view does not exist")
        if "organization_usage" in lowered:
            raise SnowflakeObjectUnavailableError("org usage view does not exist")
        for dataset_key, rows in account_datasets.items():
            if _known_account_sql_matches(dataset_key, lowered):
                return rows
        raise AssertionError(f"unexpected SQL: {sql}")

    data = build_snowflake_dashboard_data(Settings(), execute=execute)

    assert data.metadata.data_mode == "estimated"
    assert data.metadata.organization_usage.available is False
    assert data.metadata.account_usage.available is True
    assert data.datasets["org_spend_daily"] == []
    assert data.datasets["rate_sheet_daily"] == []


def test_invalid_current_account_locator_skips_org_usage_with_safe_detail() -> None:
    data = build_snowflake_dashboard_data(
        Settings(), execute=_fake_execute(account_locator="BAD-DROP")
    )

    assert data.metadata.data_mode == "estimated"
    assert data.metadata.account_locator is None
    assert data.metadata.organization_usage.available is False
    assert (
        data.metadata.organization_usage.detail
        == "Could not determine Snowflake account."
    )
    assert "BAD-DROP" not in data.metadata.organization_usage.detail
    assert data.metadata.account_usage.available is True
    assert data.datasets["org_spend_daily"] == []
    assert data.datasets["rate_sheet_daily"] == []


def test_tolerates_account_usage_failure_when_org_usage_is_available() -> None:
    data = build_snowflake_dashboard_data(
        Settings(), execute=_fake_execute(account_fails=True)
    )

    assert data.metadata.data_mode == "billed"
    assert data.metadata.organization_usage.available is True
    assert data.metadata.account_usage.available is False
    assert data.datasets["warehouse_spend_daily"] == []
    assert data.datasets["account_spend_daily"] == []


def test_fails_when_both_source_groups_are_unavailable() -> None:
    with pytest.raises(DashboardSourcesUnavailableError):
        build_snowflake_dashboard_data(
            Settings(), execute=_fake_execute(org_fails=True, account_fails=True)
        )


def test_mixed_currency_marks_dataset_unsupported() -> None:
    org_rows = _org_rows()
    org_rows["org_spend_daily"] = [
        *org_rows["org_spend_daily"],
        {
            "usage_date": date(2026, 6, 5),
            "service_type": "STORAGE",
            "rating_type": "STORAGE",
            "billing_type": "CONSUMPTION",
            "is_adjustment": False,
            "currency": "EUR",
            "spend": 4.0,
        },
    ]

    data = build_snowflake_dashboard_data(
        Settings(), execute=_fake_execute(org_rows=org_rows)
    )

    assert data.metadata.unsupported_reason == "mixed_currency"
    assert data.metadata.currency is None


def test_build_top_warehouses_table_limits_to_top_ten() -> None:
    rows = [
        {"warehouse_name": f"WH_{index:02}", "credits_used": float(index)}
        for index in range(12)
    ]

    table = build_top_warehouses_table(rows)

    assert len(table) == 10
    assert [row["warehouse_name"] for row in table] == [
        "WH_11",
        "WH_10",
        "WH_09",
        "WH_08",
        "WH_07",
        "WH_06",
        "WH_05",
        "WH_04",
        "WH_03",
        "WH_02",
    ]


def _known_account_sql_matches(dataset_key: str, sql: str) -> bool:
    markers = {
        "warehouse_spend_daily": "warehouse_metering_history",
        "service_spend_daily": "metering_daily_history",
        "query_compute_by_user_daily": "query_attribution_history",
        "database_storage_daily": "database_storage_usage_history",
    }
    return markers[dataset_key] in sql

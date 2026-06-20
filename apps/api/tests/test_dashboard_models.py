from datetime import date

import pytest

from app.models import (
    REQUIRED_DATASET_KEYS,
    SAFE_DATASET_ROW_FIELDS,
    DashboardDatasetMetadata,
    SourceAvailability,
)


def test_required_dataset_keys_match_dashboard_contract() -> None:
    # Pin the dataset keys the run contract must expose as an independent literal
    # (NOT re-derived from SAFE_DATASET_ROW_FIELDS, which would be tautological).
    # Adding or dropping a dataset without updating this set fails the test.
    assert REQUIRED_DATASET_KEYS == frozenset(
        {
            "account_spend_daily",
            "warehouse_spend_daily",
            "service_spend_daily",
            "query_compute_by_user_daily",
            "database_storage_daily",
            "top_warehouses_table",
            "org_spend_daily",
            "rate_sheet_daily",
            "capacity_balance_daily",
            "current_account",
        }
    )
    # The safe-field allowlist must cover exactly those keys: no dataset can be
    # required without an allowlist entry, and none can be allow-listed without
    # being required.
    assert frozenset(SAFE_DATASET_ROW_FIELDS) == REQUIRED_DATASET_KEYS


def test_dashboard_dataset_metadata_serializes_dates_and_validates_literals() -> None:
    metadata = DashboardDatasetMetadata(
        data_mode="billed",
        account_locator="TU24199",
        currency="USD",
        billing_through_date=date(2026, 6, 8),
        account_usage_through_date=date(2026, 6, 9),
        estimated_credit_price_usd=3.0,
        storage_price_usd_per_tb_month=23.0,
        unsupported_reason=None,
        organization_usage=SourceAvailability(available=True),
        account_usage=SourceAvailability(available=True),
    )

    dumped = metadata.model_dump(mode="json")

    assert dumped["billing_through_date"] == "2026-06-08"
    assert dumped["account_usage_through_date"] == "2026-06-09"
    assert dumped["organization_usage"] == {"available": True, "detail": None}

    with pytest.raises(ValueError):
        DashboardDatasetMetadata(
            data_mode="invoiced",
            account_locator=None,
            currency=None,
            billing_through_date=None,
            account_usage_through_date=None,
            estimated_credit_price_usd=3.0,
            storage_price_usd_per_tb_month=23.0,
            unsupported_reason=None,
            organization_usage=SourceAvailability(available=True),
            account_usage=SourceAvailability(available=True),
        )

    with pytest.raises(ValueError):
        DashboardDatasetMetadata(
            data_mode="billed",
            account_locator=None,
            currency=None,
            billing_through_date=None,
            account_usage_through_date=None,
            estimated_credit_price_usd=3.0,
            storage_price_usd_per_tb_month=23.0,
            unsupported_reason="unsupported",
            organization_usage=SourceAvailability(available=True),
            account_usage=SourceAvailability(available=True),
        )

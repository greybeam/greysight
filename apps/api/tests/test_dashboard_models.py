from datetime import date
from uuid import UUID

import pytest

from app.models import (
    REQUIRED_DATASET_KEYS,
    SAFE_DATASET_ROW_FIELDS,
    SCHEMA_VERSION,
    DashboardDatasetMetadata,
    DashboardDatasetResponse,
    DashboardRun,
    SourceAvailability,
)


def test_dashboard_dataset_contract_has_schema_version_and_new_safe_fields() -> None:
    assert SCHEMA_VERSION == 1
    assert SAFE_DATASET_ROW_FIELDS["warehouse_spend_daily"] == frozenset(
        {"usage_date", "warehouse_name", "credits_used", "credits_used_compute"}
    )
    assert SAFE_DATASET_ROW_FIELDS["query_compute_by_user_daily"] == frozenset(
        {
            "usage_date",
            "user_name",
            "warehouse_name",
            "credits_attributed_compute",
        }
    )
    assert SAFE_DATASET_ROW_FIELDS["org_spend_daily"] == frozenset(
        {
            "usage_date",
            "service_type",
            "rating_type",
            "billing_type",
            "is_adjustment",
            "currency",
            "spend",
        }
    )
    assert SAFE_DATASET_ROW_FIELDS["rate_sheet_daily"] == frozenset(
        {"usage_date", "service_type", "rating_type", "currency", "effective_rate"}
    )
    assert SAFE_DATASET_ROW_FIELDS["capacity_balance_daily"] == frozenset(
        {"usage_date", "currency", "balance"}
    )
    assert SAFE_DATASET_ROW_FIELDS["current_account"] == frozenset({"account_locator"})
    assert REQUIRED_DATASET_KEYS == frozenset(SAFE_DATASET_ROW_FIELDS)


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


def test_dashboard_dataset_response_defaults_schema_version_and_metadata() -> None:
    run = DashboardRun(
        id="run-1",
        status="completed",
        source="demo",
        window_days=30,
        organization_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    response = DashboardDatasetResponse(run=run, summary={}, datasets={})

    assert response.schema_version == SCHEMA_VERSION
    assert response.metadata is None

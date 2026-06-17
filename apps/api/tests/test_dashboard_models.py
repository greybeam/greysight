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


def test_required_dataset_keys_match_safe_field_allowlist() -> None:
    # REQUIRED_DATASET_KEYS and the per-dataset safe-field allowlist must stay in
    # sync: a key in one but not the other would silently drop a dataset from a
    # run or admit fields that are not on the allowlist.
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

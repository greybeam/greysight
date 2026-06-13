from copy import deepcopy
from datetime import date, datetime

import pytest

from app.models import DashboardDatasetMetadata, DashboardRun, SourceAvailability
from app.services.demo_data import build_demo_dashboard_dataset
from app.services.dashboard_view_builder import (
    DASHBOARD_STACKED_SERIES_LIMIT,
    DEFAULT_VIEW_WINDOW_DAYS,
    OTHER_STACKED_SERIES_LABEL,
    DashboardInvalidRangeError,
    DashboardRangeOutOfBoundsError,
    _bucket_stacked_series,
    _build_rate_index,
    _build_storage_rate_index,
    _credits_to_dollars,
    _format_bytes,
    _format_currency,
    _rate_key,
    _storage_price_for,
    _warehouse_row_dollars,
    build_dashboard_view,
    resolve_dashboard_view_range,
)
from app.services.dashboard_view_models import DashboardViewRange, DashboardViewResponse


def test_resolves_default_relative_range_from_through_date() -> None:
    view_range = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
    )

    assert DEFAULT_VIEW_WINDOW_DAYS == 30
    assert view_range == DashboardViewRange(
        mode="relative",
        window_days=30,
        start_date=date(2026, 5, 10),
        end_date=date(2026, 6, 8),
    )


def test_resolves_supported_relative_windows_from_through_date() -> None:
    seven = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
        window_days=7,
    )
    ninety = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
        window_days=90,
    )

    assert seven.start_date == date(2026, 6, 2)
    assert seven.end_date == date(2026, 6, 8)
    assert ninety.start_date == date(2026, 3, 11)
    assert ninety.end_date == date(2026, 6, 8)


def test_clamps_custom_end_date_to_through_date() -> None:
    view_range = resolve_dashboard_view_range(
        through_date=date(2026, 6, 8),
        source_start_date=date(2026, 3, 1),
        source_end_date=date(2026, 6, 8),
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 11),
    )

    assert view_range == DashboardViewRange(
        mode="custom",
        window_days=None,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 8),
    )


def test_rejects_ranges_older_than_stored_source_bounds() -> None:
    with pytest.raises(DashboardRangeOutOfBoundsError) as exc_info:
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            start_date=date(2026, 2, 28),
            end_date=date(2026, 3, 5),
        )

    assert exc_info.value.source_start_date == date(2026, 3, 1)
    assert exc_info.value.source_end_date == date(2026, 6, 8)


def test_rejects_ambiguous_relative_and_custom_range() -> None:
    with pytest.raises(DashboardInvalidRangeError, match="exactly one range mode"):
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            window_days=7,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 8),
        )


def test_rejects_unsupported_relative_window_days() -> None:
    with pytest.raises(
        DashboardInvalidRangeError,
        match="Unsupported dashboard window_days",
    ):
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            window_days=14,
        )


def test_rejects_partial_custom_range() -> None:
    with pytest.raises(
        DashboardInvalidRangeError,
        match="requires start_date and end_date",
    ):
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            start_date=date(2026, 6, 1),
        )


def test_rejects_custom_start_date_after_end_date() -> None:
    with pytest.raises(DashboardInvalidRangeError, match="on or before end_date"):
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 7),
        )


def test_rejects_custom_start_date_after_through_date_as_invalid_range() -> None:
    with pytest.raises(
        DashboardInvalidRangeError,
        match="on or before through_date",
    ) as exc_info:
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 20),
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 11),
        )

    assert type(exc_info.value) is DashboardInvalidRangeError


def test_rejects_inverted_stored_source_bounds_as_invalid_input() -> None:
    with pytest.raises(
        ValueError,
        match="Dashboard source bounds start_date must be on or before end_date",
    ) as exc_info:
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 6, 9),
            source_end_date=date(2026, 6, 8),
        )

    assert type(exc_info.value) is ValueError


def _demo_run() -> DashboardRun:
    payload = build_demo_dashboard_dataset()
    return DashboardRun.model_validate(payload.run.model_dump(mode="json"))


def _demo_datasets() -> dict[str, list[dict[str, object]]]:
    return deepcopy(build_demo_dashboard_dataset().datasets)


def _demo_metadata() -> DashboardDatasetMetadata:
    return build_demo_dashboard_dataset().metadata


def _source_bounds(datasets: dict[str, list[dict[str, object]]]) -> tuple[date, date]:
    dates = [
        date.fromisoformat(str(row["usage_date"]))
        for rows in datasets.values()
        for row in rows
        if "usage_date" in row
    ]
    return min(dates), max(dates)


def _sum_org_spend(
    rows: list[dict[str, object]], start_date: date, end_date: date
) -> float:
    return sum(
        float(row["spend"])
        for row in rows
        if row["billing_type"] == "CONSUMPTION"
        and start_date.isoformat() <= str(row["usage_date"]) <= end_date.isoformat()
    )


def _single_org_spend_view(spend: float, currency: str) -> DashboardViewResponse:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    datasets["org_spend_daily"] = [
        {
            "usage_date": "2026-06-08",
            "service_type": "CLOUD_SERVICES",
            "rating_type": "COMPUTE",
            "billing_type": "CONSUMPTION",
            "is_adjustment": spend < 0,
            "currency": currency,
            "spend": spend,
        }
    ]
    metadata = _demo_metadata().model_copy(update={"currency": currency})

    return build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )


def test_builds_demo_view_with_billed_like_totals_and_labels() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=30,
    )

    expected_total = _sum_org_spend(
        datasets["org_spend_daily"],
        view.range.start_date,
        view.range.end_date,
    )
    assert view.header.data_mode_label == "Demo"
    assert view.header.account_locator == "DEMO123"
    assert view.header.freshness_label == "Demo data through Jun 8, 2026"
    assert view.total_spend.basis == "billed"
    assert view.total_spend.total == pytest.approx(expected_total, abs=0.01)
    assert view.total_spend.total_label.startswith("$")
    assert view.unsupported is None


def test_builds_capacity_balance_from_latest_filtered_date() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    datasets["capacity_balance_daily"] = [
        {
            "usage_date": "2026-06-06",
            "currency": "USD",
            "balance": 12_250.0,
        },
        {
            "usage_date": "2026-06-07",
            "currency": "USD",
            "balance": 12_125.0,
        },
        {
            "usage_date": "2026-06-08",
            "currency": "USD",
            "balance": 11_875.25,
        },
        {
            "usage_date": "2026-06-09",
            "currency": "USD",
            "balance": 11_650.0,
        },
    ]

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 6, 6),
        end_date=date(2026, 6, 8),
    )

    assert view.capacity_balance.current_balance == pytest.approx(11_875.25, abs=0.01)
    assert view.capacity_balance.current_balance_label == "$11,875.25"
    assert view.capacity_balance.current_balance_date == "2026-06-08"
    assert [point.date for point in view.capacity_balance.daily_series] == [
        "2026-06-06",
        "2026-06-07",
        "2026-06-08",
    ]
    assert [point.balance for point in view.capacity_balance.daily_series] == [
        12_250.0,
        12_125.0,
        11_875.25,
    ]


def test_builds_billed_view_with_negative_adjustments_included() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    baseline = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )
    adjusted_datasets = deepcopy(datasets)
    adjusted_datasets["org_spend_daily"].append(
        {
            "usage_date": "2026-06-08",
            "service_type": "CLOUD_SERVICES",
            "rating_type": "COMPUTE",
            "billing_type": "CONSUMPTION",
            "is_adjustment": True,
            "currency": "USD",
            "spend": -10.0,
        }
    )

    adjusted = build_dashboard_view(
        run=_demo_run(),
        datasets=adjusted_datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )

    assert adjusted.total_spend.total == pytest.approx(
        baseline.total_spend.total - 10,
        abs=0.01,
    )


def test_negative_usd_billed_total_uses_accounting_minus_label() -> None:
    view = _single_org_spend_view(spend=-10.0, currency="USD")

    assert view.total_spend.total == pytest.approx(-10, abs=0.01)
    assert view.total_spend.total_label == "-$10.00"


def test_usd_billed_total_rounds_half_cents_like_intl() -> None:
    view = _single_org_spend_view(spend=2.675, currency="USD")

    assert view.total_spend.total == pytest.approx(2.675, abs=0.001)
    assert view.total_spend.total_label == "$2.68"


def test_negative_usd_billed_total_rounds_half_cents_like_intl() -> None:
    view = _single_org_spend_view(spend=-2.675, currency="USD")

    assert view.total_spend.total == pytest.approx(-2.675, abs=0.001)
    assert view.total_spend.total_label == "-$2.68"


def test_eur_billed_total_uses_symbol_prefix_label() -> None:
    view = _single_org_spend_view(spend=1234.5, currency="EUR")

    assert view.total_spend.total == pytest.approx(1234.5, abs=0.01)
    assert view.total_spend.total_label == "€1,234.50"


def test_eur_billed_total_rounds_half_cents_like_intl() -> None:
    view = _single_org_spend_view(spend=1.005, currency="EUR")

    assert view.total_spend.total == pytest.approx(1.005, abs=0.001)
    assert view.total_spend.total_label == "€1.01"


def test_negative_eur_billed_total_uses_symbol_prefix_label() -> None:
    view = _single_org_spend_view(spend=-10.0, currency="EUR")

    assert view.total_spend.total == pytest.approx(-10, abs=0.01)
    assert view.total_spend.total_label == "-€10.00"


def test_jpy_billed_total_uses_symbol_prefix_without_decimals() -> None:
    view = _single_org_spend_view(spend=10.0, currency="JPY")

    assert view.total_spend.total == pytest.approx(10, abs=0.01)
    assert view.total_spend.total_label == "¥10"


def test_negative_jpy_billed_total_uses_symbol_prefix_without_decimals() -> None:
    view = _single_org_spend_view(spend=-10.0, currency="JPY")

    assert view.total_spend.total == pytest.approx(-10, abs=0.01)
    assert view.total_spend.total_label == "-¥10"


def test_fractional_jpy_billed_total_trims_trailing_zeroes() -> None:
    view = _single_org_spend_view(spend=10.5, currency="JPY")

    assert view.total_spend.total == pytest.approx(10.5, abs=0.01)
    assert view.total_spend.total_label == "¥10.5"


def test_negative_fractional_jpy_billed_total_trims_trailing_zeroes() -> None:
    view = _single_org_spend_view(spend=-1234.5, currency="JPY")

    assert view.total_spend.total == pytest.approx(-1234.5, abs=0.01)
    assert view.total_spend.total_label == "-¥1,234.5"


def test_chf_billed_total_uses_code_prefix_with_nbsp() -> None:
    view = _single_org_spend_view(spend=1234.5, currency="CHF")

    assert view.total_spend.total == pytest.approx(1234.5, abs=0.01)
    assert view.total_spend.total_label == "CHF\u00a01,234.50"


def test_negative_chf_billed_total_uses_code_prefix_with_nbsp() -> None:
    view = _single_org_spend_view(spend=-1234.5, currency="CHF")

    assert view.total_spend.total == pytest.approx(-1234.5, abs=0.01)
    assert view.total_spend.total_label == "-CHF\u00a01,234.50"


def test_mxn_billed_total_uses_symbol_prefix_label() -> None:
    view = _single_org_spend_view(spend=1234.5, currency="MXN")

    assert view.total_spend.total == pytest.approx(1234.5, abs=0.01)
    assert view.total_spend.total_label == "MX$1,234.50"


def test_sek_billed_total_uses_code_prefix_with_nbsp() -> None:
    view = _single_org_spend_view(spend=1234.5, currency="SEK")

    assert view.total_spend.total == pytest.approx(1234.5, abs=0.01)
    assert view.total_spend.total_label == "SEK\u00a01,234.50"


def test_inr_billed_total_uses_symbol_prefix_label() -> None:
    view = _single_org_spend_view(spend=1234.5, currency="INR")

    assert view.total_spend.total == pytest.approx(1234.5, abs=0.01)
    assert view.total_spend.total_label == "₹1,234.50"


def test_krw_billed_total_uses_symbol_prefix_with_compact_decimals() -> None:
    view = _single_org_spend_view(spend=1234.5, currency="KRW")

    assert view.total_spend.total == pytest.approx(1234.5, abs=0.01)
    assert view.total_spend.total_label == "₩1,234.5"


def test_zar_billed_total_uses_code_prefix_with_nbsp() -> None:
    view = _single_org_spend_view(spend=1234.5, currency="ZAR")

    assert view.total_spend.total == pytest.approx(1234.5, abs=0.01)
    assert view.total_spend.total_label == "ZAR\u00a01,234.50"


def test_projection_uses_latest_30_days_regardless_of_selected_range() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)

    seven = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )
    thirty = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=30,
    )
    custom = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 8),
    )

    assert seven.total_spend.projected_monthly == pytest.approx(
        thirty.total_spend.projected_monthly,
        abs=0.01,
    )
    assert custom.total_spend.projected_monthly == pytest.approx(
        thirty.total_spend.projected_monthly,
        abs=0.01,
    )
    assert seven.total_spend.projection_basis_label == "latest 30 days"


def test_estimated_mode_uses_account_usage_through_date_and_estimated_basis() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    # NOTE: model_copy(update=...) does NOT re-validate, so pass a real
    # SourceAvailability instance here, not a dict. A raw dict would leave
    # metadata.organization_usage as a dict and break attribute access
    # (metadata.organization_usage.available) inside the builder.
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )
    datasets["org_spend_daily"] = []
    datasets["rate_sheet_daily"] = []

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )

    assert view.header.data_mode_label == "Estimated"
    assert view.header.through_date == "2026-06-08"
    assert view.total_spend.basis == "estimated"
    assert view.warehouse_spend.basis == "estimated"
    assert view.warehouse_spend.ranked_warehouses


def test_warehouse_spend_prices_compute_and_cloud_services_credits() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )
    datasets["org_spend_daily"] = []
    # Single warehouse on a single day: 10 compute credits + 4 cloud-services
    # credits (credits_used 14 - credits_used_compute 10).
    datasets["warehouse_spend_daily"] = [
        {
            "usage_date": "2026-06-08",
            "warehouse_name": "BI_WH",
            "credits_used": 14.0,
            "credits_used_compute": 10.0,
        }
    ]
    datasets["query_compute_by_user_daily"] = []
    # Distinct rates prove both service types are consulted: compute priced at
    # 2.0/credit, cloud services at 0.5/credit.
    datasets["rate_sheet_daily"] = [
        {
            "usage_date": "2026-06-08",
            "service_type": "WAREHOUSE_METERING",
            "rating_type": "COMPUTE",
            "currency": "USD",
            "effective_rate": 2.0,
        },
        {
            "usage_date": "2026-06-08",
            "service_type": "CLOUD_SERVICES",
            "rating_type": "COMPUTE",
            "currency": "USD",
            "effective_rate": 0.5,
        },
    ]

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 8),
    )

    # 10 * 2.0 (compute) + 4 * 0.5 (cloud services) = 22.0. A regression that
    # drops cloud-services credits would yield 20.0 and fail this assertion.
    expected = 10.0 * 2.0 + 4.0 * 0.5
    assert view.warehouse_spend.warehouse_names == ["BI_WH"]
    assert view.warehouse_spend.ranked_warehouses[0].spend == pytest.approx(expected)
    daily_point = view.warehouse_spend.daily_series[-1]
    assert daily_point.values["BI_WH"] == pytest.approx(expected)


def test_estimated_non_usd_rate_returns_prepared_unsupported_view() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "currency": "USD",
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )
    datasets["org_spend_daily"] = []
    datasets["service_spend_daily"] = [
        {
            "usage_date": "2026-06-08",
            "service_type": "WAREHOUSE_METERING",
            "credits_used": 10.0,
        }
    ]
    datasets["rate_sheet_daily"] = [
        {
            "usage_date": "2026-06-08",
            "service_type": "WAREHOUSE_METERING",
            "rating_type": "COMPUTE",
            "currency": "EUR",
            "effective_rate": 2.5,
        }
    ]

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )

    assert view.unsupported is not None
    assert view.unsupported.title == "Estimated non-USD spend is not supported"
    assert view.total_spend.total == 0
    assert view.total_spend.is_empty is True


def test_no_through_date_returns_empty_view_before_range_validation() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={
            "billing_through_date": None,
            "account_usage_through_date": None,
        }
    )

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 2),
    )

    assert view.total_spend.is_empty is True
    assert view.range.start_date == source_end
    assert view.range.end_date == source_end


def test_build_dashboard_view_normalizes_datetime_usage_dates() -> None:
    datasets = _demo_datasets()
    metadata = _demo_metadata().model_copy(
        update={"billing_through_date": date(2026, 6, 8)}
    )
    datasets["org_spend_daily"] = [
        {
            "usage_date": date(2026, 6, 7),
            "service_type": "CLOUD_SERVICES",
            "rating_type": "COMPUTE",
            "billing_type": "CONSUMPTION",
            "is_adjustment": False,
            "currency": "USD",
            "spend": 10.0,
        },
        {
            "usage_date": datetime(2026, 6, 8, 14, 30),
            "service_type": "WAREHOUSE_METERING",
            "rating_type": "COMPUTE",
            "billing_type": "CONSUMPTION",
            "is_adjustment": False,
            "currency": "USD",
            "spend": 20.0,
        },
    ]

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=date(2026, 6, 7),
        source_end_date=date(2026, 6, 8),
        start_date=date(2026, 6, 7),
        end_date=date(2026, 6, 8),
    )

    assert view.total_spend.total == pytest.approx(30.0, abs=0.01)
    assert [point.date for point in view.total_spend.daily_series] == [
        "2026-06-07",
        "2026-06-08",
    ]


def test_mixed_currency_returns_prepared_unsupported_view() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={"currency": None, "unsupported_reason": "mixed_currency"}
    )

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=30,
    )

    assert view.unsupported is not None
    assert view.unsupported.title == "Mixed currencies are not supported"
    assert view.total_spend.is_empty is True


def test_mixed_currency_out_of_bounds_custom_range_returns_unsupported() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={"currency": None, "unsupported_reason": "mixed_currency"}
    )

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 2),
    )

    assert view.unsupported is not None
    assert view.unsupported.title == "Mixed currencies are not supported"
    assert view.total_spend.is_empty is True


def test_estimated_non_usd_out_of_bounds_custom_range_returns_unsupported() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "currency": "EUR",
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )
    datasets["org_spend_daily"] = []
    datasets["rate_sheet_daily"] = []

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 2),
    )

    assert view.unsupported is not None
    assert view.unsupported.title == "Estimated non-USD spend is not supported"
    assert view.total_spend.is_empty is True


def test_estimated_non_usd_rate_out_of_bounds_custom_range_returns_unsupported() -> (
    None
):
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "currency": "USD",
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )
    datasets["org_spend_daily"] = []
    datasets["rate_sheet_daily"] = [
        {
            "usage_date": "2026-06-08",
            "service_type": "WAREHOUSE_METERING",
            "rating_type": "COMPUTE",
            "currency": "EUR",
            "effective_rate": 2.5,
        }
    ]

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 2),
    )

    assert view.unsupported is not None
    assert view.unsupported.title == "Estimated non-USD spend is not supported"
    assert view.total_spend.is_empty is True


def test_uncapped_ranked_bars_and_detail_rows_match_dashboard_limits() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    service_rows = []
    for index in range(55):
        service_number = index + 1
        service_rows.append(
            {
                "usage_date": "2026-06-08",
                "service_type": f"SERVICE_{service_number:02}",
                "rating_type": "COMPUTE",
                "billing_type": "CONSUMPTION",
                "is_adjustment": False,
                "currency": "USD",
                "spend": float(service_number),
            }
        )
    datasets["org_spend_daily"] = service_rows

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )

    # Ranked lists and bars are uncapped: every entry is present and rendered.
    assert len(view.service_spend.ranked_services) == 55
    assert len(view.service_spend.service_bars) == 55
    assert view.service_spend.service_bars[0].name == "SERVICE_55"
    assert view.service_spend.service_bars[0].bar_width_percent == 100
    # Only the stacked chart series is bucketed: top 13 + an "Other" bucket.
    assert len(view.service_spend.service_names) == 14
    assert view.service_spend.service_names[-1] == "Other"
    # Detail tables keep their own independent cap.
    assert len(view.detail_tables.services) == 50


def test_missing_required_billed_spend_fails_loudly() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    datasets["org_spend_daily"] = [
        {
            "usage_date": "2026-06-08",
            "service_type": "CLOUD_SERVICES",
            "rating_type": "COMPUTE",
            "billing_type": "CONSUMPTION",
            "is_adjustment": False,
            "currency": "USD",
        }
    ]

    with pytest.raises(ValueError, match="org_spend_daily.spend"):
        build_dashboard_view(
            run=_demo_run(),
            datasets=datasets,
            metadata=_demo_metadata(),
            source_start_date=source_start,
            source_end_date=source_end,
            window_days=7,
        )


def test_missing_required_rate_sheet_effective_rate_fails_loudly() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    datasets["rate_sheet_daily"] = [
        {
            "usage_date": "2026-06-08",
            "service_type": "WAREHOUSE_METERING",
            "rating_type": "COMPUTE",
            "currency": "USD",
        }
    ]

    with pytest.raises(ValueError, match="rate_sheet_daily.effective_rate"):
        build_dashboard_view(
            run=_demo_run(),
            datasets=datasets,
            metadata=_demo_metadata(),
            source_start_date=source_start,
            source_end_date=source_end,
            window_days=7,
        )


@pytest.mark.parametrize(
    "storage_row",
    [
        {
            "usage_date": "2026-06-08",
            "database_name": "RAW",
            "average_database_bytes": 1_000_000_000_000,
            "average_failsafe_bytes": None,
        },
        {
            "usage_date": "2026-06-08",
            "database_name": "RAW",
            "average_database_bytes": 1_000_000_000_000,
        },
    ],
)
def test_nullable_or_absent_failsafe_bytes_counts_as_zero(
    storage_row: dict[str, object],
) -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    datasets["org_spend_daily"] = []
    datasets["rate_sheet_daily"] = []
    datasets["database_storage_daily"] = [storage_row]
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "storage_price_usd_per_tb_month": 30.0,
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 8),
    )

    assert view.storage_spend.daily_series[0].spend == pytest.approx(1.0, abs=0.01)
    assert view.storage_spend.databases[0].bytes == 1_000_000_000_000


def test_missing_required_database_storage_bytes_fails_loudly() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    datasets["org_spend_daily"] = []
    datasets["rate_sheet_daily"] = []
    datasets["database_storage_daily"] = [
        {
            "usage_date": "2026-06-08",
            "database_name": "RAW",
            "average_failsafe_bytes": 0,
        }
    ]
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )

    with pytest.raises(
        ValueError, match="database_storage_daily.average_database_bytes"
    ):
        build_dashboard_view(
            run=_demo_run(),
            datasets=datasets,
            metadata=metadata,
            source_start_date=source_start,
            source_end_date=source_end,
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 8),
        )


@pytest.mark.parametrize("invalid_spend", [True, "nan", "inf"])
def test_invalid_required_billed_spend_fails_loudly(invalid_spend: object) -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    datasets["org_spend_daily"] = [
        {
            "usage_date": "2026-06-08",
            "service_type": "CLOUD_SERVICES",
            "rating_type": "COMPUTE",
            "billing_type": "CONSUMPTION",
            "is_adjustment": False,
            "currency": "USD",
            "spend": invalid_spend,
        }
    ]

    with pytest.raises(ValueError, match="org_spend_daily.spend"):
        build_dashboard_view(
            run=_demo_run(),
            datasets=datasets,
            metadata=_demo_metadata(),
            source_start_date=source_start,
            source_end_date=source_end,
            window_days=7,
        )


def test_bucket_stacked_series_keeps_series_at_or_under_limit_untouched() -> None:
    names = [f"S{index}" for index in range(DASHBOARD_STACKED_SERIES_LIMIT)]
    values_by_date = [{name: float(index) for index, name in enumerate(names)}]

    bucketed_names, bucketed_values = _bucket_stacked_series(names, values_by_date)

    assert bucketed_names == names
    assert "Other" not in bucketed_names
    assert bucketed_values == values_by_date
    # Inputs are returned as new objects, not aliased.
    assert bucketed_values is not values_by_date
    assert bucketed_values[0] is not values_by_date[0]


def test_bucket_stacked_series_keeps_real_other_as_normal_category_under_limit() -> (
    None
):
    # At/under the limit, a real category named "Other" (not last, nonzero) stays
    # a normal category: order is unchanged (not pinned last) and values are not
    # aggregated into a synthetic bucket.
    names = ["S0", OTHER_STACKED_SERIES_LABEL, "S1", "S2"]
    values_by_date = [
        {"S0": 1.0, OTHER_STACKED_SERIES_LABEL: 2.0, "S1": 3.0, "S2": 4.0},
        {"S0": 5.0, OTHER_STACKED_SERIES_LABEL: 6.0, "S1": 7.0, "S2": 8.0},
    ]

    bucketed_names, bucketed_values = _bucket_stacked_series(names, values_by_date)

    # Incoming order is preserved verbatim; "Other" is not moved last.
    assert bucketed_names == names
    assert bucketed_values == values_by_date


def test_bucket_stacked_series_buckets_overflow_into_top_thirteen_plus_other() -> None:
    # 16 categories (> 14). Total spend ranks them by their trailing index, so
    # the smallest three (S0, S1, S2) collapse into "Other".
    names = [f"S{index}" for index in range(16)]
    values_by_date = [
        {name: float(index) for index, name in enumerate(names)},
        {name: float(index) * 2 for index, name in enumerate(names)},
    ]

    bucketed_names, bucketed_values = _bucket_stacked_series(names, values_by_date)

    # Top 13 by descending total, then "Other" pinned last.
    assert bucketed_names == [
        "S15",
        "S14",
        "S13",
        "S12",
        "S11",
        "S10",
        "S9",
        "S8",
        "S7",
        "S6",
        "S5",
        "S4",
        "S3",
        "Other",
    ]
    assert len(bucketed_names) == DASHBOARD_STACKED_SERIES_LIMIT
    # "Other" aggregates the dropped categories per date: S0 + S1 + S2.
    assert bucketed_values[0]["Other"] == pytest.approx(0.0 + 1.0 + 2.0)
    assert bucketed_values[1]["Other"] == pytest.approx(0.0 + 2.0 + 4.0)
    # Kept categories preserve their original per-date values.
    assert bucketed_values[0]["S15"] == pytest.approx(15.0)
    assert bucketed_values[1]["S15"] == pytest.approx(30.0)


def test_bucket_stacked_series_merges_real_other_into_synthetic_bucket() -> None:
    # 16 categories (> 14) including a real "Other" that ranks high enough to
    # otherwise land in the top 13. When bucketing engages it must never occupy
    # a kept slot; its values fold into the synthetic bucket so the reserved name
    # appears exactly once (last) with no overwrite or duplicate key.
    names = [f"S{index}" for index in range(15)] + [OTHER_STACKED_SERIES_LABEL]
    values_by_date = [
        {f"S{index}": float(index) for index in range(15)}
        | {OTHER_STACKED_SERIES_LABEL: 100.0},
        {f"S{index}": float(index) * 2 for index in range(15)}
        | {OTHER_STACKED_SERIES_LABEL: 200.0},
    ]

    bucketed_names, bucketed_values = _bucket_stacked_series(names, values_by_date)

    # Reserved name appears exactly once, pinned last; no duplicate keys.
    assert bucketed_names.count(OTHER_STACKED_SERIES_LABEL) == 1
    assert bucketed_names[-1] == OTHER_STACKED_SERIES_LABEL
    assert len(bucketed_names) == len(set(bucketed_names))
    assert len(bucketed_names) == DASHBOARD_STACKED_SERIES_LIMIT
    # Top 13 by descending total exclude the real "Other"; smallest S0, S1 drop.
    assert bucketed_names == [
        "S14",
        "S13",
        "S12",
        "S11",
        "S10",
        "S9",
        "S8",
        "S7",
        "S6",
        "S5",
        "S4",
        "S3",
        "S2",
        OTHER_STACKED_SERIES_LABEL,
    ]
    # Synthetic bucket = real "Other" + ranked-out remainder (S0 + S1).
    assert bucketed_values[0][OTHER_STACKED_SERIES_LABEL] == pytest.approx(
        100.0 + 0.0 + 1.0
    )
    assert bucketed_values[1][OTHER_STACKED_SERIES_LABEL] == pytest.approx(
        200.0 + 0.0 + 2.0
    )
    # Per-date sums are preserved across the rebucketing.
    for original, bucketed in zip(values_by_date, bucketed_values, strict=True):
        assert sum(bucketed.values()) == pytest.approx(sum(original.values()))


def test_warehouse_row_dollars_raises_on_negative_cloud_credits() -> None:
    # credits_used below credits_used_compute is an impossible negative cloud
    # balance; the guard must fail loud (not a stripped assert).
    row = {
        "usage_date": "2026-06-08",
        "warehouse_name": "BI_WH",
        "credits_used": 8.0,
        "credits_used_compute": 10.0,
    }

    def convert(
        credits: float, usage_date: date, service_type: str, rating_type: str | None
    ) -> float:
        return credits

    with pytest.raises(
        ValueError,
        match="warehouse_spend_daily credits_used must be >= credits_used_compute",
    ):
        _warehouse_row_dollars(row, convert)


def test_stacked_service_series_buckets_but_ranked_lists_stay_full() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    service_rows = [
        {
            "usage_date": "2026-06-08",
            "service_type": f"SERVICE_{index + 1:02}",
            "rating_type": "COMPUTE",
            "billing_type": "CONSUMPTION",
            "is_adjustment": False,
            "currency": "USD",
            "spend": float(index + 1),
        }
        for index in range(20)
    ]
    datasets["org_spend_daily"] = service_rows

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=7,
    )

    # Stacked chart series is bucketed to 13 + Other.
    assert len(view.service_spend.service_names) == DASHBOARD_STACKED_SERIES_LIMIT
    assert view.service_spend.service_names[-1] == "Other"
    for point in view.service_spend.daily_series:
        assert set(point.values) == set(view.service_spend.service_names)
    # Ranked lists stay unbucketed (full data, no synthetic "Other").
    assert len(view.service_spend.ranked_services) == 20
    assert all(row.name != "Other" for row in view.service_spend.ranked_services)


def test_warehouse_total_sums_window_daily_dollars() -> None:
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )
    datasets["org_spend_daily"] = []
    datasets["warehouse_spend_daily"] = [
        {
            "usage_date": "2026-06-07",
            "warehouse_name": "BI_WH",
            "credits_used": 10.0,
            "credits_used_compute": 10.0,
        },
        {
            "usage_date": "2026-06-08",
            "warehouse_name": "ETL_WH",
            "credits_used": 6.0,
            "credits_used_compute": 6.0,
        },
    ]
    datasets["query_compute_by_user_daily"] = []
    datasets["rate_sheet_daily"] = [
        {
            "usage_date": usage_date,
            "service_type": "WAREHOUSE_METERING",
            "rating_type": "COMPUTE",
            "currency": "USD",
            "effective_rate": 2.0,
        }
        for usage_date in ("2026-06-07", "2026-06-08")
    ]

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=date(2026, 6, 7),
        end_date=date(2026, 6, 8),
    )

    # 10 credits * 2.0 + 6 credits * 2.0 = 32.0 across the window, matching the
    # sum of the stacked daily series.
    expected = 10.0 * 2.0 + 6.0 * 2.0
    chart_total = sum(
        amount
        for point in view.warehouse_spend.daily_series
        for amount in point.values.values()
    )
    assert view.warehouse_spend.total == pytest.approx(expected)
    assert view.warehouse_spend.total == pytest.approx(chart_total)
    assert view.warehouse_spend.total_label == "$32.00"


# ---------------------------------------------------------------------------
# Storage Spend backend rework — new-feature coverage.
# ---------------------------------------------------------------------------


def _estimated_storage_view(
    *,
    storage_rows: list[dict[str, object]],
    rate_rows: list[dict[str, object]],
    storage_price_usd_per_tb_month: float = 25.0,
    start_date: date = date(2026, 6, 8),
    end_date: date = date(2026, 6, 8),
    account_usage_through_date: date = date(2026, 6, 8),
) -> DashboardViewResponse:
    """Build an estimated-basis view isolated to the storage path.

    org_spend_daily is cleared so the storage daily_series follows the estimated
    branch (the per-date rate-sheet/hybrid grid) rather than billed totals.
    """
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)
    source_end = max(source_end, account_usage_through_date)
    datasets["org_spend_daily"] = []
    datasets["rate_sheet_daily"] = rate_rows
    datasets["database_storage_daily"] = storage_rows
    metadata = _demo_metadata().model_copy(
        update={
            "data_mode": "estimated",
            "billing_through_date": None,
            "account_usage_through_date": account_usage_through_date,
            "storage_price_usd_per_tb_month": storage_price_usd_per_tb_month,
            "organization_usage": SourceAvailability(
                available=False, detail="org unavailable"
            ),
        }
    )
    return build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=metadata,
        source_start_date=source_start,
        source_end_date=source_end,
        start_date=start_date,
        end_date=end_date,
    )


# --- 1. Rate index max-dedup regression -----------------------------------


def test_rate_index_dedups_grain_rows_to_max_effective_rate() -> None:
    # rate_sheet_daily now carries usage_type in its grain, so the same
    # (date, service_type, rating_type) can appear twice differing only by
    # usage_type. The index must collapse them to the MAX effective_rate,
    # replicating the old SQL max() so the grain change can't shift pricing.
    usage_date = date(2026, 6, 8)
    rows = [
        {
            "usage_date": usage_date.isoformat(),
            "service_type": "WAREHOUSE_METERING",
            "usage_type": "compute",
            "rating_type": "COMPUTE",
            "currency": "USD",
            "effective_rate": 2.0,
        },
        {
            "usage_date": usage_date.isoformat(),
            "service_type": "WAREHOUSE_METERING",
            "usage_type": "cloud_services",
            "rating_type": "COMPUTE",
            "currency": "USD",
            "effective_rate": 3.0,
        },
    ]

    index = _build_rate_index(rows)

    rating_key = _rate_key(usage_date, "WAREHOUSE_METERING", "COMPUTE")
    service_key = _rate_key(usage_date, "WAREHOUSE_METERING")
    assert index[rating_key].effective_rate == 3.0
    assert index[service_key].effective_rate == 3.0


def test_rate_index_grain_change_matches_single_row_baseline_pricing() -> None:
    # The dollar pricing produced from the deduped two-row grain must equal the
    # pricing from a single max-rate baseline row: the grain change is invisible
    # to warehouse/service costing.
    usage_date = date(2026, 6, 8)
    metadata = _demo_metadata()

    baseline_index = _build_rate_index(
        [
            {
                "usage_date": usage_date.isoformat(),
                "service_type": "WAREHOUSE_METERING",
                "usage_type": "compute",
                "rating_type": "COMPUTE",
                "currency": "USD",
                "effective_rate": 3.0,
            }
        ]
    )
    grain_index = _build_rate_index(
        [
            {
                "usage_date": usage_date.isoformat(),
                "service_type": "WAREHOUSE_METERING",
                "usage_type": "compute",
                "rating_type": "COMPUTE",
                "currency": "USD",
                "effective_rate": 2.0,
            },
            {
                "usage_date": usage_date.isoformat(),
                "service_type": "WAREHOUSE_METERING",
                "usage_type": "cloud_services",
                "rating_type": "COMPUTE",
                "currency": "USD",
                "effective_rate": 3.0,
            },
        ]
    )

    def dollars(index: dict[str, object]) -> float | None:
        return _credits_to_dollars(
            credits=10.0,
            usage_date=usage_date,
            service_type="WAREHOUSE_METERING",
            rates=index,
            metadata=metadata,
            rating_type="COMPUTE",
        )

    assert dollars(grain_index) == dollars(baseline_index)
    assert dollars(grain_index) == pytest.approx(30.0)


# --- 2. Storage rate lookup -----------------------------------------------


def test_storage_rate_sheet_row_drives_daily_and_monthly_cost() -> None:
    # 100 TB of cost-bearing bytes at a rate-sheet storage rate of 25/TB-month.
    # Daily = 100 * 25 / 30 ; monthly (ranking) = 100 * 25.
    storage_rows = [
        {
            "usage_date": "2026-06-08",
            "database_name": "RAW",
            "average_database_bytes": 100_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": 0,
        }
    ]
    rate_rows = [
        {
            "usage_date": "2026-06-08",
            "service_type": "STORAGE",
            "usage_type": "storage",
            "rating_type": "STORAGE",
            "currency": "USD",
            "effective_rate": 25.0,
        }
    ]

    view = _estimated_storage_view(
        storage_rows=storage_rows,
        rate_rows=rate_rows,
        storage_price_usd_per_tb_month=999.0,  # must be ignored when rate present
    )

    assert view.storage_spend.daily_series[0].spend == pytest.approx(
        100.0 * 25.0 / 30.0
    )
    assert view.storage_spend.total == pytest.approx(100.0 * 25.0 / 30.0)
    assert view.storage_spend.databases[0].monthly_spend == pytest.approx(100.0 * 25.0)


def test_storage_falls_back_to_metadata_price_when_no_rate_row() -> None:
    # No storage rate-sheet row => fall back to metadata price.
    storage_rows = [
        {
            "usage_date": "2026-06-08",
            "database_name": "RAW",
            "average_database_bytes": 100_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": 0,
        }
    ]

    view = _estimated_storage_view(
        storage_rows=storage_rows,
        rate_rows=[],
        storage_price_usd_per_tb_month=30.0,
    )

    assert view.storage_spend.daily_series[0].spend == pytest.approx(
        100.0 * 30.0 / 30.0
    )
    assert view.storage_spend.databases[0].monthly_spend == pytest.approx(100.0 * 30.0)


def test_storage_monthly_spend_uses_price_on_each_databases_latest_date() -> None:
    # The storage rate CHANGES during the window (20/TB-month on 06-08, then
    # 25/TB-month on 06-09). RAW's latest row is on 06-08 (an EARLIER date than
    # the global latest storage date of 06-09, which belongs to STAGING).
    # RAW's monthly_spend must pair its size snapshot with the price in effect on
    # ITS OWN latest date (20), not the global latest-date price (25).
    storage_rows = [
        {
            "usage_date": "2026-06-08",
            "database_name": "RAW",
            "average_database_bytes": 100_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": 0,
        },
        {
            "usage_date": "2026-06-09",
            "database_name": "STAGING",
            "average_database_bytes": 50_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": 0,
        },
    ]
    rate_rows = [
        {
            "usage_date": "2026-06-08",
            "service_type": "STORAGE",
            "usage_type": "storage",
            "rating_type": "STORAGE",
            "currency": "USD",
            "effective_rate": 20.0,
        },
        {
            "usage_date": "2026-06-09",
            "service_type": "STORAGE",
            "usage_type": "storage",
            "rating_type": "STORAGE",
            "currency": "USD",
            "effective_rate": 25.0,
        },
    ]

    view = _estimated_storage_view(
        storage_rows=storage_rows,
        rate_rows=rate_rows,
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 9),
        account_usage_through_date=date(2026, 6, 9),
    )

    databases = {row.name: row for row in view.storage_spend.databases}
    # RAW is priced from its own latest date (06-08 => 20), NOT the global
    # latest-date price (06-09 => 25).
    assert databases["RAW"].monthly_spend == pytest.approx(100.0 * 20.0)
    # STAGING sits on the global latest date, so its price is unchanged.
    assert databases["STAGING"].monthly_spend == pytest.approx(50.0 * 25.0)


@pytest.mark.parametrize("usage_type", ["Storage", "STORAGE", "storage"])
def test_storage_rate_usage_type_match_is_case_insensitive(usage_type: str) -> None:
    storage_rates = _build_storage_rate_index(
        [
            {
                "usage_date": "2026-06-08",
                "service_type": "STORAGE",
                "usage_type": usage_type,
                "rating_type": "STORAGE",
                "currency": "USD",
                "effective_rate": 25.0,
            }
        ]
    )
    metadata = _demo_metadata().model_copy(
        update={"storage_price_usd_per_tb_month": 30.0}
    )

    # The rate-sheet rate (25) is preferred over the metadata fallback (30).
    assert _storage_price_for(date(2026, 6, 8), storage_rates, metadata) == 25.0


def test_storage_rate_index_dedups_to_max_per_date() -> None:
    storage_rates = _build_storage_rate_index(
        [
            {
                "usage_date": "2026-06-08",
                "service_type": "STORAGE",
                "usage_type": "storage",
                "rating_type": "STORAGE",
                "currency": "USD",
                "effective_rate": 20.0,
            },
            {
                "usage_date": "2026-06-08",
                "service_type": "STORAGE",
                "usage_type": "storage",
                "rating_type": "STORAGE",
                "currency": "USD",
                "effective_rate": 25.0,
            },
        ]
    )

    assert storage_rates[date(2026, 6, 8)].effective_rate == 25.0


# --- 3. Hybrid bytes -------------------------------------------------------


@pytest.mark.parametrize(
    "storage_row",
    [
        {
            "usage_date": "2026-06-08",
            "database_name": "RAW",
            "average_database_bytes": 100_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": None,
        },
        {
            "usage_date": "2026-06-08",
            "database_name": "RAW",
            "average_database_bytes": 100_000_000_000_000,
            "average_failsafe_bytes": 0,
        },
    ],
)
def test_nullable_or_absent_hybrid_bytes_counts_as_zero(
    storage_row: dict[str, object],
) -> None:
    rate_rows = [
        {
            "usage_date": "2026-06-08",
            "service_type": "STORAGE",
            "usage_type": "storage",
            "rating_type": "STORAGE",
            "currency": "USD",
            "effective_rate": 25.0,
        }
    ]

    view = _estimated_storage_view(storage_rows=[storage_row], rate_rows=rate_rows)

    # Only the 100 TB database bytes count; hybrid is treated as zero.
    assert view.storage_spend.databases[0].bytes == pytest.approx(100_000_000_000_000)
    assert view.storage_spend.daily_series[0].spend == pytest.approx(
        100.0 * 25.0 / 30.0
    )


def test_present_hybrid_bytes_included_in_series_and_per_database() -> None:
    # database + failsafe + hybrid all contribute to cost-bearing bytes.
    storage_rows = [
        {
            "usage_date": "2026-06-08",
            "database_name": "RAW",
            "average_database_bytes": 100_000_000_000_000,
            "average_failsafe_bytes": 8_000_000_000_000,
            "average_hybrid_table_storage_bytes": 3_000_000_000_000,
        }
    ]
    rate_rows = [
        {
            "usage_date": "2026-06-08",
            "service_type": "STORAGE",
            "usage_type": "storage",
            "rating_type": "STORAGE",
            "currency": "USD",
            "effective_rate": 25.0,
        }
    ]

    view = _estimated_storage_view(storage_rows=storage_rows, rate_rows=rate_rows)

    total_tb = 100 + 8 + 3  # 111 TB
    assert view.storage_spend.databases[0].bytes == pytest.approx(111_000_000_000_000)
    assert view.storage_spend.databases[0].monthly_spend == pytest.approx(
        total_tb * 25.0
    )
    assert view.storage_spend.daily_series[0].spend == pytest.approx(
        total_tb * 25.0 / 30.0
    )
    # The per-database stacked point reflects the same hybrid-inclusive dollars.
    assert view.storage_spend.database_daily_series[0].values["RAW"] == pytest.approx(
        total_tb * 25.0 / 30.0
    )


# --- 4. Stacked series construction ---------------------------------------


def test_storage_stacked_series_values_match_daily_dollars() -> None:
    storage_rows = [
        {
            "usage_date": "2026-06-08",
            "database_name": name,
            "average_database_bytes": tb * 1_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": 0,
        }
        for name, tb in (("RAW", 100), ("ANALYTICS", 50))
    ]
    rate_rows = [
        {
            "usage_date": "2026-06-08",
            "service_type": "STORAGE",
            "usage_type": "storage",
            "rating_type": "STORAGE",
            "currency": "USD",
            "effective_rate": 25.0,
        }
    ]

    view = _estimated_storage_view(storage_rows=storage_rows, rate_rows=rate_rows)

    point = view.storage_spend.database_daily_series[0]
    assert point.values["RAW"] == pytest.approx(100.0 * 25.0 / 30.0)
    assert point.values["ANALYTICS"] == pytest.approx(50.0 * 25.0 / 30.0)
    # Overall daily series equals the sum of the per-database stacked values.
    assert view.storage_spend.daily_series[0].spend == pytest.approx(
        sum(point.values.values())
    )


def test_storage_period_spend_sums_to_total_and_sorts_desc() -> None:
    # Two databases over a two-day window. period_spend per database = the sum of
    # its daily storage dollars across the window (the same grid as the KPI
    # total), so the per-database period_spend values MUST sum to total.
    storage_rows = [
        {
            "usage_date": usage_date,
            "database_name": name,
            "average_database_bytes": tb * 1_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": 0,
        }
        for usage_date in ("2026-06-08", "2026-06-09")
        # ANALYTICS is larger per day, so it must rank first by period_spend even
        # though both appear on the latest day.
        for name, tb in (("RAW", 40), ("ANALYTICS", 100))
    ]
    rate_rows = [
        {
            "usage_date": usage_date,
            "service_type": "STORAGE",
            "usage_type": "storage",
            "rating_type": "STORAGE",
            "currency": "USD",
            "effective_rate": 25.0,
        }
        for usage_date in ("2026-06-08", "2026-06-09")
    ]

    view = _estimated_storage_view(
        storage_rows=storage_rows,
        rate_rows=rate_rows,
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 9),
        account_usage_through_date=date(2026, 6, 9),
    )

    databases = view.storage_spend.databases
    # Per-database period_spend sums to the KPI total.
    assert sum(row.period_spend for row in databases) == pytest.approx(
        view.storage_spend.total
    )
    # Sorted by period_spend DESC: ANALYTICS (100 TB/day) outranks RAW (40 TB/day).
    assert [row.name for row in databases] == ["ANALYTICS", "RAW"]
    period_spends = [row.period_spend for row in databases]
    assert period_spends == sorted(period_spends, reverse=True)
    # ANALYTICS: 100 TB * 25/TB-month / 30 days/month * 2 days.
    assert databases[0].period_spend == pytest.approx(100.0 * 25.0 / 30.0 * 2)
    assert databases[0].period_spend_label == _format_currency(
        databases[0].period_spend, "USD"
    )
    # monthly_spend stays the latest-day snapshot estimate (kept for the detail
    # table) and is distinct from the windowed period_spend.
    assert databases[0].monthly_spend == pytest.approx(100.0 * 25.0)
    # detail_tables.storage carries the same rows, so it gets period_spend too.
    assert view.detail_tables.storage[0].period_spend == pytest.approx(
        databases[0].period_spend
    )


def test_storage_databases_include_dbs_absent_on_latest_date() -> None:
    # STALE_DB has storage rows only on early window dates (none on the latest
    # date). Its window spend is still part of period_spend_by_db and total, so
    # it MUST appear in storage_spend.databases and the per-database period_spend
    # values MUST still sum to the KPI total.
    storage_rows = [
        # STALE_DB only on the first day; LIVE_DB on both days.
        {
            "usage_date": "2026-06-08",
            "database_name": "STALE_DB",
            "average_database_bytes": 40 * 1_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": 0,
        },
        {
            "usage_date": "2026-06-08",
            "database_name": "LIVE_DB",
            "average_database_bytes": 100 * 1_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": 0,
        },
        {
            "usage_date": "2026-06-09",
            "database_name": "LIVE_DB",
            "average_database_bytes": 100 * 1_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": 0,
        },
    ]
    rate_rows = [
        {
            "usage_date": usage_date,
            "service_type": "STORAGE",
            "usage_type": "storage",
            "rating_type": "STORAGE",
            "currency": "USD",
            "effective_rate": 25.0,
        }
        for usage_date in ("2026-06-08", "2026-06-09")
    ]

    view = _estimated_storage_view(
        storage_rows=storage_rows,
        rate_rows=rate_rows,
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 9),
        account_usage_through_date=date(2026, 6, 9),
    )

    databases = view.storage_spend.databases
    names = {row.name for row in databases}
    # STALE_DB has no row on the latest date but still has window spend.
    assert "STALE_DB" in names
    assert "LIVE_DB" in names
    # Per-database period_spend still reconciles with the KPI total.
    assert round(sum(d.period_spend for d in databases), 2) == round(
        view.storage_spend.total, 2
    )


def test_demo_storage_section_not_marked_empty() -> None:
    # Demo mode resolves to the billed basis, but the demo dataset has no billed
    # STORAGE rows. Emptiness must follow the estimated grid total (what the
    # storage section actually renders) rather than the all-zero billed series.
    datasets = _demo_datasets()
    source_start, source_end = _source_bounds(datasets)

    view = build_dashboard_view(
        run=_demo_run(),
        datasets=datasets,
        metadata=_demo_metadata(),
        source_start_date=source_start,
        source_end_date=source_end,
        window_days=30,
    )

    assert view.storage_spend.is_empty is False
    assert view.storage_spend.total > 0
    assert len(view.storage_spend.databases) > 0


def test_storage_stacked_series_buckets_overflow_into_thirteen_plus_other() -> None:
    # 16 databases (> 14) => top 13 by total kept, rest folded into "Other"
    # (last). Per-date totals are conserved across bucketing.
    storage_rows = [
        {
            "usage_date": "2026-06-08",
            "database_name": f"DB_{index:02}",
            # Larger index => more bytes => higher rank.
            "average_database_bytes": (index + 1) * 1_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": 0,
        }
        for index in range(16)
    ]
    rate_rows = [
        {
            "usage_date": "2026-06-08",
            "service_type": "STORAGE",
            "usage_type": "storage",
            "rating_type": "STORAGE",
            "currency": "USD",
            "effective_rate": 25.0,
        }
    ]

    view = _estimated_storage_view(storage_rows=storage_rows, rate_rows=rate_rows)

    names = view.storage_spend.database_names
    assert len(names) == DASHBOARD_STACKED_SERIES_LIMIT
    assert names[-1] == OTHER_STACKED_SERIES_LABEL
    assert names.count(OTHER_STACKED_SERIES_LABEL) == 1
    # 16 databases, 13 kept => the 3 smallest (DB_00, DB_01, DB_02) fold into
    # "Other".
    point = view.storage_spend.database_daily_series[0]
    assert "DB_00" not in point.values
    assert "DB_01" not in point.values
    assert "DB_02" not in point.values
    assert point.values[OTHER_STACKED_SERIES_LABEL] == pytest.approx(
        (1 + 2 + 3) * 25.0 / 30.0
    )
    # Total is conserved: sum of stacked values equals the overall daily spend
    # and the pre-bucketing KPI total.
    stacked_sum = sum(point.values.values())
    assert view.storage_spend.daily_series[0].spend == pytest.approx(stacked_sum)
    expected_total = sum(range(1, 17)) * 25.0 / 30.0
    assert view.storage_spend.total == pytest.approx(expected_total)


# --- 5. total / total_label ------------------------------------------------


def test_storage_total_sums_all_databases_across_window() -> None:
    # Two dates, two databases. total == sum of every per-database daily dollar
    # across the window (pre-bucketing); total_label == _format_currency(total).
    storage_rows = [
        {
            "usage_date": usage_date,
            "database_name": name,
            "average_database_bytes": tb * 1_000_000_000_000,
            "average_failsafe_bytes": 0,
            "average_hybrid_table_storage_bytes": 0,
        }
        for usage_date in ("2026-06-07", "2026-06-08")
        for name, tb in (("RAW", 100), ("ANALYTICS", 50))
    ]
    rate_rows = [
        {
            "usage_date": usage_date,
            "service_type": "STORAGE",
            "usage_type": "storage",
            "rating_type": "STORAGE",
            "currency": "USD",
            "effective_rate": 25.0,
        }
        for usage_date in ("2026-06-07", "2026-06-08")
    ]

    view = _estimated_storage_view(
        storage_rows=storage_rows,
        rate_rows=rate_rows,
        start_date=date(2026, 6, 7),
        end_date=date(2026, 6, 8),
    )

    per_day = (100 + 50) * 25.0 / 30.0
    expected_total = per_day * 2
    assert view.storage_spend.total == pytest.approx(expected_total)
    assert view.storage_spend.total_label == _format_currency(
        view.storage_spend.total, "USD"
    )
    # And the total equals the sum of the overall daily series.
    assert view.storage_spend.total == pytest.approx(
        sum(point.spend for point in view.storage_spend.daily_series)
    )


# --- 6. _format_bytes unit tests ------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0.0 B"),
        (512, "512.0 B"),
        (1000, "1.0 KB"),
        (1500, "1.5 KB"),
        (1_000_000, "1.0 MB"),
        (1_000_000_000, "1.0 GB"),
        (1_000_000_000_000, "1.0 TB"),
        (10_539_124_266_240, "10.5 TB"),
        (1_000_000_000_000_000, "1.0 PB"),
        (2_500_000_000_000_000, "2.5 PB"),
        # PB is the largest unit: very large values stay in PB, never overflow.
        (5_000_000_000_000_000_000, "5000.0 PB"),
        (-1500, "-1.5 KB"),
    ],
)
def test_format_bytes_contract(value: float, expected: str) -> None:
    assert _format_bytes(value) == expected


# --- 7. Demo data shape ----------------------------------------------------


def test_demo_rate_rows_carry_usage_type_with_per_date_storage() -> None:
    datasets = _demo_datasets()
    rate_rows = datasets["rate_sheet_daily"]

    assert all("usage_type" in row for row in rate_rows)
    storage_dates = {
        row["usage_date"]
        for row in rate_rows
        if str(row["usage_type"]).casefold() == "storage"
    }
    all_dates = {row["usage_date"] for row in rate_rows}
    # Every usage_date has at least one "storage" rate row.
    assert storage_dates == all_dates


def test_demo_storage_rows_carry_hybrid_bytes() -> None:
    datasets = _demo_datasets()
    storage_rows = datasets["database_storage_daily"]

    assert storage_rows
    assert all("average_hybrid_table_storage_bytes" in row for row in storage_rows)

from copy import deepcopy
from datetime import date

import pytest

from app.models import DashboardDatasetMetadata, DashboardRun, SourceAvailability
from app.services.demo_data import build_demo_dashboard_dataset
from app.services.dashboard_view_builder import (
    DEFAULT_VIEW_WINDOW_DAYS,
    DashboardRangeOutOfBoundsError,
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
    with pytest.raises(ValueError, match="exactly one range mode"):
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            window_days=7,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 8),
        )


def test_rejects_unsupported_relative_window_days() -> None:
    with pytest.raises(ValueError, match="Unsupported dashboard window_days"):
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            window_days=14,
        )


def test_rejects_partial_custom_range() -> None:
    with pytest.raises(ValueError, match="requires start_date and end_date"):
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            start_date=date(2026, 6, 1),
        )


def test_rejects_custom_start_date_after_end_date() -> None:
    with pytest.raises(ValueError, match="on or before end_date"):
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 8),
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 7),
        )


def test_rejects_custom_start_date_after_through_date_as_invalid_range() -> None:
    with pytest.raises(ValueError, match="on or before through_date") as exc_info:
        resolve_dashboard_view_range(
            through_date=date(2026, 6, 8),
            source_start_date=date(2026, 3, 1),
            source_end_date=date(2026, 6, 20),
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 11),
        )

    assert type(exc_info.value) is ValueError


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
    assert view.compute_spend.compute_basis == "estimated"
    assert view.compute_spend.ranked_warehouses


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


def test_capped_bars_and_detail_rows_match_dashboard_limits() -> None:
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

    assert len(view.service_spend.ranked_services) == 55
    assert len(view.service_spend.service_bars) == 8
    assert view.service_spend.service_bars[0].name == "SERVICE_55"
    assert view.service_spend.service_bars[0].bar_width_percent == 100
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

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
from typing import Any, Callable

from app.models import DashboardDatasetMetadata, DashboardRun
from app.services.dashboard_view_models import (
    BalancePoint,
    CapacityBalanceViewModel,
    DashboardProjectionRange,
    DashboardViewRange,
    DashboardViewResponse,
    DetailTablesViewModel,
    DollarPoint,
    HeaderViewModel,
    RankedBarRow,
    RankedSpendRow,
    ServicePoint,
    ServiceSpendViewModel,
    SpendBasis,
    StorageDatabasePoint,
    StorageDatabaseRow,
    StorageSpendViewModel,
    TotalSpendViewModel,
    UnsupportedViewModel,
    UserDetailRow,
    WarehouseDetailRow,
    WarehousePoint,
    WarehouseSpendViewModel,
)

DEFAULT_VIEW_WINDOW_DAYS = 30
SUPPORTED_VIEW_WINDOW_DAYS = frozenset({7, 30, 90})
DASHBOARD_DETAIL_ROW_LIMIT = 50
# Stacked chart series cap. Series beyond this count are bucketed into the top
# (LIMIT - 1) categories by total spend plus an aggregated "Other" series. This
# matches the frontend chart palette length in apps/web/src/lib/chart-colors.ts
# (SERIES_PALETTE has 14 entries); keep the two in sync.
DASHBOARD_STACKED_SERIES_LIMIT = 14
OTHER_STACKED_SERIES_LABEL = "Other"
CURRENCY_SYMBOL_PREFIXES = {
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "KRW": "₩",
    "CAD": "CA$",
    "AUD": "A$",
    "NZD": "NZ$",
    "MXN": "MX$",
    "INR": "₹",
    "CNY": "CN¥",
    "HKD": "HK$",
    "BRL": "R$",
    "ILS": "₪",
    "TWD": "NT$",
    "PHP": "₱",
}
CURRENCY_CODE_PREFIXES = frozenset(
    {
        "CHF",
        "CZK",
        "DKK",
        "HUF",
        "IDR",
        "MYR",
        "NOK",
        "PLN",
        "SEK",
        "SGD",
        "THB",
        "TRY",
        "ZAR",
    }
)
CURRENCY_CODE_SEPARATOR = "\u00a0"
CURRENCY_COMPACT_DECIMAL_CODES = frozenset({"HUF", "IDR", "JPY", "KRW"})
CURRENCY_TWO_DECIMAL_QUANT = Decimal("0.01")
# Tolerance for the cloud-services credits invariant so floating-point noise in
# (credits_used - credits_used_compute) is not mistaken for an impossible state.
_FLOAT_EPSILON = 1e-9

DatasetRow = dict[str, Any]
RateIndex = dict[str, "RateIndexEntry"]
ConvertCredits = Callable[[float, date, str, str | None], float]


@dataclass(frozen=True)
class DashboardRangeOutOfBoundsError(ValueError):
    source_start_date: date
    source_end_date: date

    def __str__(self) -> str:
        return "Requested dashboard range is outside stored source bounds."


class DashboardInvalidRangeError(ValueError):
    pass


@dataclass(frozen=True)
class RateIndexEntry:
    currency: str | None
    effective_rate: float


@dataclass(frozen=True)
class NamedAmount:
    name: str
    spend: float
    credits: float


def window_start_for(through_date: date, window_days: int) -> date:
    return through_date - timedelta(days=window_days - 1)


def projection_range_for(through_date: date) -> tuple[date, date]:
    return window_start_for(through_date, DEFAULT_VIEW_WINDOW_DAYS), through_date


def resolve_dashboard_view_range(
    *,
    through_date: date,
    source_start_date: date,
    source_end_date: date,
    window_days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DashboardViewRange:
    if source_start_date > source_end_date:
        raise ValueError(
            "Dashboard source bounds start_date must be on or before end_date."
        )

    has_relative = window_days is not None
    has_custom = start_date is not None or end_date is not None
    if has_relative and has_custom:
        raise DashboardInvalidRangeError(
            "Dashboard view accepts exactly one range mode."
        )
    if has_custom and (start_date is None or end_date is None):
        raise DashboardInvalidRangeError(
            "Custom dashboard range requires start_date and end_date."
        )

    if not has_relative and not has_custom:
        window_days = DEFAULT_VIEW_WINDOW_DAYS
        has_relative = True

    if has_relative:
        if window_days not in SUPPORTED_VIEW_WINDOW_DAYS:
            raise DashboardInvalidRangeError("Unsupported dashboard window_days.")
        effective_start = window_start_for(through_date, int(window_days))
        effective_end = through_date
        mode = "relative"
        effective_window_days = int(window_days)
    else:
        assert start_date is not None
        assert end_date is not None
        if start_date > end_date:
            raise DashboardInvalidRangeError(
                "Custom dashboard range start_date must be on or before end_date."
            )
        if start_date > through_date:
            raise DashboardInvalidRangeError(
                "Custom dashboard range start_date must be on or before through_date."
            )
        effective_start = start_date
        effective_end = min(end_date, through_date)
        mode = "custom"
        effective_window_days = None

    if effective_start < source_start_date or effective_end > source_end_date:
        raise DashboardRangeOutOfBoundsError(
            source_start_date=source_start_date,
            source_end_date=source_end_date,
        )
    if effective_start > effective_end:
        raise DashboardRangeOutOfBoundsError(
            source_start_date=source_start_date,
            source_end_date=source_end_date,
        )

    return DashboardViewRange(
        mode=mode,
        window_days=effective_window_days,
        start_date=effective_start,
        end_date=effective_end,
    )


def build_dashboard_view(
    *,
    run: DashboardRun,
    datasets: dict[str, list[DatasetRow]],
    metadata: DashboardDatasetMetadata,
    source_start_date: date,
    source_end_date: date,
    window_days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DashboardViewResponse:
    through_date = _through_date_for(metadata)
    currency = metadata.currency or "USD"
    header = _build_header_view_model(metadata, currency, through_date)
    if through_date is None:
        return _empty_dashboard_view(
            run=run,
            view_range=DashboardViewRange(
                mode="relative",
                window_days=DEFAULT_VIEW_WINDOW_DAYS,
                start_date=source_end_date,
                end_date=source_end_date,
            ),
            projection_range=DashboardProjectionRange(
                start_date=source_end_date,
                end_date=source_end_date,
            ),
            header=header,
            currency=currency,
            unsupported=None,
        )

    unsupported = _unsupported_view_model(metadata)
    if unsupported is not None:
        view_range = resolve_dashboard_view_range(
            through_date=through_date,
            source_start_date=date.min,
            source_end_date=date.max,
            window_days=window_days,
            start_date=start_date,
            end_date=end_date,
        )
        projection_start, projection_end = projection_range_for(through_date)
        return _empty_dashboard_view(
            run=run,
            view_range=view_range,
            projection_range=DashboardProjectionRange(
                start_date=projection_start,
                end_date=projection_end,
            ),
            header=header,
            currency=currency,
            unsupported=unsupported,
        )

    rates = _build_rate_index(_dataset_rows(datasets, "rate_sheet_daily"))
    is_billed = metadata.data_mode in {"billed", "demo"}
    unsupported = _estimated_conversion_unsupported_view_model(
        metadata=metadata,
        rates=rates,
        is_billed=is_billed,
    )
    if unsupported is not None:
        view_range = resolve_dashboard_view_range(
            through_date=through_date,
            source_start_date=date.min,
            source_end_date=date.max,
            window_days=window_days,
            start_date=start_date,
            end_date=end_date,
        )
        projection_start, projection_end = projection_range_for(through_date)
        return _empty_dashboard_view(
            run=run,
            view_range=view_range,
            projection_range=DashboardProjectionRange(
                start_date=projection_start,
                end_date=projection_end,
            ),
            header=header,
            currency=currency,
            unsupported=unsupported,
        )

    view_range = resolve_dashboard_view_range(
        through_date=through_date,
        source_start_date=source_start_date,
        source_end_date=source_end_date,
        window_days=window_days,
        start_date=start_date,
        end_date=end_date,
    )
    projection_start, projection_end = projection_range_for(through_date)
    return _build_dashboard_view_for_ranges(
        run=run,
        datasets=datasets,
        metadata=metadata,
        header=header,
        currency=currency,
        rates=rates,
        is_billed=is_billed,
        view_range=view_range,
        projection_range=DashboardProjectionRange(
            start_date=projection_start,
            end_date=projection_end,
        ),
    )


def _build_dashboard_view_for_ranges(
    *,
    run: DashboardRun,
    datasets: dict[str, list[DatasetRow]],
    metadata: DashboardDatasetMetadata,
    header: HeaderViewModel,
    currency: str,
    rates: RateIndex,
    is_billed: bool,
    view_range: DashboardViewRange,
    projection_range: DashboardProjectionRange,
) -> DashboardViewResponse:
    basis: SpendBasis = "billed" if is_billed else "estimated"

    consumption_rows = [
        row
        for row in _dataset_rows(datasets, "org_spend_daily")
        if _is_consumption_spend_row(row)
    ]
    billed_rows = _rows_in_window(
        consumption_rows, view_range.start_date, view_range.end_date
    )
    projection_billed_rows = _rows_in_window(
        consumption_rows, projection_range.start_date, projection_range.end_date
    )
    service_rows = _rows_in_window(
        _dataset_rows(datasets, "service_spend_daily"),
        view_range.start_date,
        view_range.end_date,
    )
    projection_service_rows = _rows_in_window(
        _dataset_rows(datasets, "service_spend_daily"),
        projection_range.start_date,
        projection_range.end_date,
    )
    warehouse_rows = _rows_in_window(
        _dataset_rows(datasets, "warehouse_spend_daily"),
        view_range.start_date,
        view_range.end_date,
    )
    user_rows = _rows_in_window(
        _dataset_rows(datasets, "query_compute_by_user_daily"),
        view_range.start_date,
        view_range.end_date,
    )
    storage_rows = _rows_in_window(
        _dataset_rows(datasets, "database_storage_daily"),
        view_range.start_date,
        view_range.end_date,
    )
    capacity_balance_rows = _rows_in_window(
        _dataset_rows(datasets, "capacity_balance_daily"),
        view_range.start_date,
        view_range.end_date,
    )
    billed_storage_rows = [row for row in billed_rows if _is_storage_spend_row(row)]
    dates = _date_range(view_range.start_date, view_range.end_date)
    projection_dates = _date_range(
        projection_range.start_date, projection_range.end_date
    )

    def convert(
        credits: float,
        usage_date: date,
        service_type: str,
        rating_type: str | None = None,
    ) -> float:
        return (
            _credits_to_dollars(
                credits=credits,
                usage_date=usage_date,
                service_type=service_type,
                rates=rates,
                metadata=metadata,
                rating_type=rating_type,
            )
            or 0.0
        )

    total_daily = (
        _daily_billed_totals(dates, billed_rows, currency)
        if is_billed
        else _daily_estimated_totals(dates, service_rows, currency, convert)
    )
    projection_daily = (
        _daily_billed_totals(projection_dates, projection_billed_rows, currency)
        if is_billed
        else _daily_estimated_totals(
            projection_dates, projection_service_rows, currency, convert
        )
    )
    service_spend = _build_service_spend(
        dates=dates,
        rows=billed_rows if is_billed else service_rows,
        basis=basis,
        currency=currency,
        convert=convert,
    )
    warehouse_spend = _build_warehouse_spend(
        dates=dates,
        warehouse_rows=warehouse_rows,
        user_rows=user_rows,
        currency=currency,
        convert=convert,
    )
    storage_rates = _build_storage_rate_index(
        _dataset_rows(datasets, "rate_sheet_daily")
    )
    storage_spend = _build_storage_spend(
        dates=dates,
        rows=storage_rows,
        billed_rows=billed_storage_rows,
        basis=basis,
        storage_rates=storage_rates,
        metadata=metadata,
        currency=currency,
    )
    total_spend = _build_total_spend(
        daily_series=total_daily,
        projection_daily_series=projection_daily,
        ranked_services=service_spend.ranked_services,
        basis=basis,
        currency=currency,
        day_count=len(dates),
    )
    forecast_daily_spend = (
        _trailing_average_spend(projection_daily) if is_billed else 0.0
    )
    capacity_balance = _build_capacity_balance(
        rows=capacity_balance_rows,
        currency=currency,
        forecast_daily_spend=forecast_daily_spend,
    )

    return DashboardViewResponse(
        run=run,
        range=view_range,
        projection_range=projection_range,
        header=header,
        unsupported=None,
        capacity_balance=capacity_balance,
        total_spend=total_spend,
        warehouse_spend=warehouse_spend,
        storage_spend=storage_spend,
        service_spend=service_spend,
        detail_tables=DetailTablesViewModel(
            services=_cap_detail_rows(service_spend.ranked_services),
            warehouses=_cap_detail_rows(
                _build_warehouse_details(warehouse_rows, currency, convert)
            ),
            users=_cap_detail_rows(
                [
                    UserDetailRow(
                        **row.model_dump(),
                        warehouse_name=_user_warehouse_label(user_rows, row.name),
                    )
                    for row in warehouse_spend.ranked_users
                ]
            ),
            storage=_cap_detail_rows(storage_spend.databases),
        ),
    )


def _through_date_for(metadata: DashboardDatasetMetadata) -> date | None:
    if metadata.data_mode == "estimated":
        return metadata.account_usage_through_date
    return metadata.billing_through_date or metadata.account_usage_through_date


def _format_currency(value: float, currency: str | None) -> str:
    resolved_currency = currency or "USD"
    decimal_value = _decimal_from_number(value)
    absolute_value = abs(decimal_value)
    sign = "-" if decimal_value < 0 else ""

    if resolved_currency == "USD":
        amount = _format_fixed_decimal(absolute_value)
        return f"{sign}${amount}"
    if resolved_currency in CURRENCY_SYMBOL_PREFIXES:
        amount = _format_currency_amount(absolute_value, resolved_currency)
        return f"{sign}{CURRENCY_SYMBOL_PREFIXES[resolved_currency]}{amount}"
    if resolved_currency in CURRENCY_CODE_PREFIXES:
        amount = _format_currency_amount(absolute_value, resolved_currency)
        return f"{sign}{resolved_currency}{CURRENCY_CODE_SEPARATOR}{amount}"
    amount = _format_fixed_decimal(decimal_value)
    return f"{amount} {resolved_currency}"


def _decimal_from_number(value: float) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Dashboard currency value must be finite.") from exc
    if not decimal_value.is_finite():
        raise ValueError("Dashboard currency value must be finite.")
    return decimal_value


def _format_fixed_decimal(value: Decimal) -> str:
    rounded = value.quantize(CURRENCY_TWO_DECIMAL_QUANT, rounding=ROUND_HALF_UP)
    return f"{rounded:,.2f}"


def _format_compact_amount(value: Decimal) -> str:
    rounded = value.quantize(CURRENCY_TWO_DECIMAL_QUANT, rounding=ROUND_HALF_UP)
    return f"{rounded:,.2f}".rstrip("0").rstrip(".")


def _format_currency_amount(value: Decimal, currency: str) -> str:
    if currency in CURRENCY_COMPACT_DECIMAL_CODES:
        return _format_compact_amount(value)
    return _format_fixed_decimal(value)


def _format_usage_date(value: date) -> str:
    return f"{value:%b} {value.day}, {value.year}"


_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_BYTE_UNIT_STEP = 1000.0


def _format_bytes(bytes_value: float) -> str:
    """Humanize a byte count with 1000-base decimal units and one decimal place.

    Uses decimal (1000-base) units — B, KB, MB, GB, TB, PB — so the displayed
    size visually agrees with the TB-based cost math (bytes / 1e12). Values at or
    above 1 PB stay in PB rather than overflowing the unit table.
    """
    magnitude = abs(bytes_value)
    unit_index = 0
    scaled = magnitude
    while scaled >= _BYTE_UNIT_STEP and unit_index < len(_BYTE_UNITS) - 1:
        scaled /= _BYTE_UNIT_STEP
        unit_index += 1
    sign = "-" if bytes_value < 0 else ""
    return f"{sign}{scaled:.1f} {_BYTE_UNITS[unit_index]}"


def _build_header_view_model(
    metadata: DashboardDatasetMetadata,
    currency: str,
    through_date: date | None,
) -> HeaderViewModel:
    through_date_label = _format_usage_date(through_date) if through_date else None

    return HeaderViewModel(
        data_mode_label=_data_mode_label(metadata),
        account_locator=metadata.account_locator,
        currency=currency,
        through_date=through_date.isoformat() if through_date else None,
        through_date_label=through_date_label,
        freshness_label=_build_freshness_label(metadata, through_date_label),
        estimated_credit_price_label=_format_currency(
            metadata.estimated_credit_price_usd, currency
        ),
        storage_price_label=_format_currency(
            metadata.storage_price_usd_per_tb_month, currency
        ),
    )


def _build_freshness_label(
    metadata: DashboardDatasetMetadata, through_date_label: str | None
) -> str | None:
    if through_date_label is None:
        return None
    if metadata.data_mode == "demo":
        return f"Demo data through {through_date_label}"
    if metadata.data_mode == "estimated" or metadata.billing_through_date is None:
        return f"Account Usage data through {through_date_label}"
    return f"Billing data through {through_date_label}"


def _data_mode_label(metadata: DashboardDatasetMetadata) -> str:
    if metadata.data_mode == "demo":
        return "Demo"
    if metadata.data_mode == "billed":
        return "Billed"
    return "Estimated"


def _unsupported_view_model(
    metadata: DashboardDatasetMetadata,
) -> UnsupportedViewModel | None:
    if metadata.unsupported_reason == "mixed_currency":
        return UnsupportedViewModel(
            title="Mixed currencies are not supported",
            detail="Select an account with a single billing currency to view spend.",
        )
    if (
        metadata.data_mode == "estimated"
        and metadata.currency is not None
        and not _is_usd_or_unspecified(metadata.currency)
    ):
        return _estimated_non_usd_unsupported_view_model()
    return None


def _estimated_conversion_unsupported_view_model(
    *,
    metadata: DashboardDatasetMetadata,
    rates: RateIndex,
    is_billed: bool,
) -> UnsupportedViewModel | None:
    if is_billed:
        return None
    if _is_usd_or_unspecified(metadata.currency) and all(
        _is_usd_or_unspecified(rate.currency) for rate in rates.values()
    ):
        return None
    return _estimated_non_usd_unsupported_view_model()


def _estimated_non_usd_unsupported_view_model() -> UnsupportedViewModel:
    return UnsupportedViewModel(
        title="Estimated non-USD spend is not supported",
        detail=(
            "Estimated spend requires USD rate sheet data until currency conversion "
            "is supported."
        ),
    )


def _empty_dashboard_view(
    *,
    run: DashboardRun,
    view_range: DashboardViewRange,
    projection_range: DashboardProjectionRange,
    header: HeaderViewModel,
    currency: str,
    unsupported: UnsupportedViewModel | None,
) -> DashboardViewResponse:
    return DashboardViewResponse(
        run=run,
        range=view_range,
        projection_range=projection_range,
        header=header,
        unsupported=unsupported,
        capacity_balance=_empty_capacity_balance(currency),
        total_spend=_empty_total_spend(currency),
        warehouse_spend=_empty_warehouse_spend(currency),
        storage_spend=_empty_storage_spend(currency),
        service_spend=_empty_service_spend(),
        detail_tables=DetailTablesViewModel(
            services=[],
            warehouses=[],
            users=[],
            storage=[],
        ),
    )


def _empty_total_spend(currency: str) -> TotalSpendViewModel:
    return TotalSpendViewModel(
        basis="estimated",
        total=0,
        total_label=_format_currency(0, currency),
        average_daily=0,
        average_daily_label=_format_currency(0, currency),
        projected_monthly=0,
        projected_monthly_label=_format_currency(0, currency),
        projection_basis_label="0 days",
        daily_series=[],
        top_driver=None,
        is_empty=True,
    )


def _empty_capacity_balance(currency: str) -> CapacityBalanceViewModel:
    return CapacityBalanceViewModel(
        current_balance=0,
        current_balance_label=_format_currency(0, currency),
        current_balance_date=None,
        daily_series=[],
        is_empty=True,
    )


def _empty_warehouse_spend(currency: str) -> WarehouseSpendViewModel:
    return WarehouseSpendViewModel(
        basis="estimated",
        total=0,
        total_label=_format_currency(0, currency),
        daily_series=[],
        warehouse_names=[],
        ranked_warehouses=[],
        ranked_users=[],
        warehouse_bars=[],
        user_bars=[],
        is_empty=True,
    )


def _empty_storage_spend(currency: str) -> StorageSpendViewModel:
    return StorageSpendViewModel(
        basis="estimated",
        database_basis="estimated",
        total=0,
        total_label=_format_currency(0, currency),
        daily_series=[],
        database_names=[],
        database_daily_series=[],
        databases=[],
        database_bars=[],
        is_empty=True,
    )


def _empty_service_spend() -> ServiceSpendViewModel:
    return ServiceSpendViewModel(
        basis="estimated",
        daily_series=[],
        service_names=[],
        ranked_services=[],
        service_bars=[],
        is_empty=True,
    )


def _date_range(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def _rows_in_window(
    rows: list[DatasetRow],
    start_date: date,
    end_date: date,
) -> list[DatasetRow]:
    return [
        row for row in rows if start_date <= _as_date(row["usage_date"]) <= end_date
    ]


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return datetime.fromisoformat(str(value)).date()


def _dataset_rows(
    datasets: dict[str, list[DatasetRow]], dataset_key: str
) -> list[DatasetRow]:
    return datasets.get(dataset_key, [])


def _build_rate_index(rows: list[DatasetRow]) -> RateIndex:
    rates: RateIndex = {}
    # Tracks whether the value currently stored under each service-only key came
    # from a COMPUTE row. COMPUTE is the preferred fallback rate, so a COMPUTE row
    # always supersedes a non-COMPUTE one; among rows of the same preference we
    # keep the max effective_rate.
    service_only_is_compute: dict[str, bool] = {}
    for row in rows:
        usage_date = _as_date(row["usage_date"])
        service_type = _string_field(row, "service_type", "Unknown service")
        rating_type = _optional_string(row.get("rating_type"))
        # rate_sheet_daily now carries usage_type in its grain, so multiple rows
        # can share (date, service_type, rating_type) and differ only by
        # usage_type. The SQL previously collapsed these with max(effective_rate);
        # replicate that here so the priced rate is stable regardless of grain.
        entry = RateIndexEntry(
            currency=_optional_string(row.get("currency")),
            effective_rate=_required_float_field(
                row, "rate_sheet_daily", "effective_rate"
            ),
        )

        if rating_type is not None:
            rating_key = _rate_key(usage_date, service_type, rating_type)
            rates[rating_key] = _max_rate_entry(rates.get(rating_key), entry)

        service_only_key = _rate_key(usage_date, service_type)
        is_compute = rating_type == "COMPUTE"
        stored_is_compute = service_only_is_compute.get(service_only_key)
        if service_only_key not in rates:
            rates[service_only_key] = entry
            service_only_is_compute[service_only_key] = is_compute
        elif is_compute and not stored_is_compute:
            # COMPUTE supersedes a non-COMPUTE fallback regardless of rate.
            rates[service_only_key] = entry
            service_only_is_compute[service_only_key] = True
        elif is_compute == bool(stored_is_compute):
            # Same preference tier: keep the larger effective_rate, mirroring the
            # old SQL max() aggregation across usage_type rows.
            rates[service_only_key] = _max_rate_entry(rates[service_only_key], entry)
    return rates


def _max_rate_entry(
    existing: RateIndexEntry | None, candidate: RateIndexEntry
) -> RateIndexEntry:
    if existing is None:
        return candidate
    if candidate.effective_rate > existing.effective_rate:
        return candidate
    return existing


def _rate_key(
    usage_date: date, service_type: str, rating_type: str | None = None
) -> str:
    if rating_type:
        return f"{usage_date.isoformat()}|{service_type}|{rating_type}"
    return f"{usage_date.isoformat()}|{service_type}"


def _is_usd_or_unspecified(currency: str | None) -> bool:
    return currency is None or currency == "USD"


def _credits_to_dollars(
    *,
    credits: float,
    usage_date: date,
    service_type: str,
    rates: RateIndex,
    metadata: DashboardDatasetMetadata,
    rating_type: str | None = None,
) -> float | None:
    rate = None
    if rating_type:
        rate = rates.get(_rate_key(usage_date, service_type, rating_type))
    rate = rate or rates.get(_rate_key(usage_date, service_type))

    if rate is not None:
        if not _is_usd_or_unspecified(rate.currency):
            return None
        return credits * rate.effective_rate

    if _is_usd_or_unspecified(metadata.currency):
        return credits * metadata.estimated_credit_price_usd
    return None


STORAGE_USAGE_TYPE = "storage"


def _build_storage_rate_index(rows: list[DatasetRow]) -> dict[date, RateIndexEntry]:
    """Per-date storage rate (currency-per-TB-month) from rate_sheet_daily.

    Only rows whose usage_type matches Snowflake's lowercase "storage" value
    (compared case-insensitively) are considered. When several storage rows share
    a usage_date (e.g. different service_type), the max effective_rate is kept,
    mirroring the old SQL max() aggregation and the credit-rate index.
    """
    storage_rates: dict[date, RateIndexEntry] = {}
    for row in rows:
        usage_type = _optional_string(row.get("usage_type"))
        if usage_type is None or usage_type.casefold() != STORAGE_USAGE_TYPE:
            continue
        usage_date = _as_date(row["usage_date"])
        entry = RateIndexEntry(
            currency=_optional_string(row.get("currency")),
            effective_rate=_required_float_field(
                row, "rate_sheet_daily", "effective_rate"
            ),
        )
        storage_rates[usage_date] = _max_rate_entry(
            storage_rates.get(usage_date), entry
        )
    return storage_rates


def _storage_price_for(
    usage_date: date,
    storage_rates: dict[date, RateIndexEntry],
    metadata: DashboardDatasetMetadata,
) -> float | None:
    """Resolve the storage rate (currency-per-TB-month) for a date.

    Prefers the rate-sheet rate; falls back to the configured
    storage_price_usd_per_tb_month when absent, mirroring the credit-price
    fallback in ``_credits_to_dollars``. Non-USD rate-sheet/dashboard currencies
    are unconvertible and yield ``None`` (the estimated view is already gated to
    USD upstream, but the check is mirrored here for safety).
    """
    entry = storage_rates.get(usage_date)
    if entry is not None:
        if not _is_usd_or_unspecified(entry.currency):
            return None
        return entry.effective_rate
    if _is_usd_or_unspecified(metadata.currency):
        return metadata.storage_price_usd_per_tb_month
    return None


def _storage_bytes_to_daily_dollars(
    bytes_value: float, price_per_tb_month: float
) -> float:
    return (bytes_value / 1_000_000_000_000) * (price_per_tb_month / 30)


def _daily_billed_totals(
    dates: list[date], rows: list[DatasetRow], currency: str
) -> list[DollarPoint]:
    spend_by_date = {usage_date: 0.0 for usage_date in dates}
    for row in rows:
        usage_date = _as_date(row["usage_date"])
        spend_by_date[usage_date] = spend_by_date.get(
            usage_date, 0.0
        ) + _required_float_field(row, "org_spend_daily", "spend")
    return [
        DollarPoint(
            date=usage_date.isoformat(),
            spend=spend_by_date[usage_date],
            spend_label=_format_currency(spend_by_date[usage_date], currency),
        )
        for usage_date in dates
    ]


def _daily_estimated_totals(
    dates: list[date],
    rows: list[DatasetRow],
    currency: str,
    convert: ConvertCredits,
) -> list[DollarPoint]:
    spend_by_date = {usage_date: 0.0 for usage_date in dates}
    for row in rows:
        usage_date = _as_date(row["usage_date"])
        spend_by_date[usage_date] = spend_by_date.get(usage_date, 0.0) + convert(
            _required_float_field(row, "service_spend_daily", "credits_used"),
            usage_date,
            _string_field(row, "service_type", "Unknown service"),
            None,
        )
    return [
        DollarPoint(
            date=usage_date.isoformat(),
            spend=spend_by_date[usage_date],
            spend_label=_format_currency(spend_by_date[usage_date], currency),
        )
        for usage_date in dates
    ]


def _build_total_spend(
    *,
    daily_series: list[DollarPoint],
    projection_daily_series: list[DollarPoint],
    ranked_services: list[RankedSpendRow],
    basis: SpendBasis,
    currency: str,
    day_count: int,
) -> TotalSpendViewModel:
    total = sum(row.spend for row in daily_series)
    average_daily = total / day_count if day_count > 0 else 0
    projected_monthly = (
        sum(row.spend for row in projection_daily_series) / DEFAULT_VIEW_WINDOW_DAYS
    ) * 30

    return TotalSpendViewModel(
        basis=basis,
        total=total,
        total_label=_format_currency(total, currency),
        average_daily=average_daily,
        average_daily_label=_format_currency(average_daily, currency),
        projected_monthly=projected_monthly,
        projected_monthly_label=_format_currency(projected_monthly, currency),
        projection_basis_label="latest 30 days",
        daily_series=daily_series,
        top_driver=ranked_services[0] if ranked_services else None,
        is_empty=all(row.spend == 0 for row in daily_series),
    )


MAX_FORECAST_DAYS = 1825  # ~5 years; bounds the payload if the runway is implausibly long
FORECAST_AVERAGE_WINDOW_DAYS = 7


def _trailing_average_spend(daily: list[DollarPoint]) -> float:
    """Mean spend over the trailing FORECAST_AVERAGE_WINDOW_DAYS of a daily series."""
    window = daily[-FORECAST_AVERAGE_WINDOW_DAYS:]
    if not window:
        return 0.0
    return sum(point.spend for point in window) / len(window)


def _build_forecast_series(
    *,
    current_balance: float,
    current_date: date,
    forecast_daily_spend: float,
    currency: str,
) -> list[BalancePoint]:
    """Project the balance forward at a flat daily spend until it reaches zero.

    Returns an empty list when there is nothing to forecast (non-positive spend
    or balance) or when the runway exceeds MAX_FORECAST_DAYS. The first point is
    the current (date, balance) so the forecast line joins the historical line;
    the final point lands exactly on zero (clamped).
    """
    if forecast_daily_spend <= 0 or current_balance <= 0:
        return []

    days_to_zero = math.ceil(current_balance / forecast_daily_spend)
    if days_to_zero > MAX_FORECAST_DAYS:
        return []

    points: list[BalancePoint] = []
    for offset in range(days_to_zero + 1):
        point_date = current_date + timedelta(days=offset)
        balance = max(current_balance - forecast_daily_spend * offset, 0.0)
        points.append(
            BalancePoint(
                date=point_date.isoformat(),
                balance=balance,
                balance_label=_format_currency(balance, currency),
            )
        )
    return points


def _build_capacity_balance(
    *,
    rows: list[DatasetRow],
    currency: str,
    forecast_daily_spend: float = 0.0,
) -> CapacityBalanceViewModel:
    balance_by_date: dict[date, float] = {}
    for row in rows:
        if _optional_string(row.get("currency")) != currency:
            continue
        usage_date = _as_date(row["usage_date"])
        balance_by_date[usage_date] = balance_by_date.get(
            usage_date, 0.0
        ) + _required_float_field(row, "capacity_balance_daily", "balance")

    if not balance_by_date:
        return _empty_capacity_balance(currency)

    sorted_dates = sorted(balance_by_date)
    daily_series = [
        BalancePoint(
            date=usage_date.isoformat(),
            balance=balance_by_date[usage_date],
            balance_label=_format_currency(balance_by_date[usage_date], currency),
        )
        for usage_date in sorted_dates
    ]
    current_date = sorted_dates[-1]
    current_point = daily_series[-1]
    forecast_series = _build_forecast_series(
        current_balance=current_point.balance,
        current_date=current_date,
        forecast_daily_spend=forecast_daily_spend,
        currency=currency,
    )

    return CapacityBalanceViewModel(
        current_balance=current_point.balance,
        current_balance_label=current_point.balance_label,
        current_balance_date=current_point.date,
        daily_series=daily_series,
        forecast_series=forecast_series,
        is_empty=False,
    )


def _bucket_stacked_series(
    names: list[str],
    values_by_date: list[dict[str, float]],
) -> tuple[list[str], list[dict[str, float]]]:
    """Cap a stacked daily series at DASHBOARD_STACKED_SERIES_LIMIT categories.

    When the number of categories exceeds the limit, the top (LIMIT - 1)
    categories by total spend across the whole window are kept and every other
    category is aggregated, per date, into a single "Other" series appended last.
    A real category named OTHER_STACKED_SERIES_LABEL is never eligible for the
    kept top set when bucketing engages; its per-date values are folded into the
    synthetic bucket so the output never carries the reserved name twice.
    Categories at or under the limit are returned unchanged (a real "Other" stays
    a normal category). Both the names list and each date's values dict are
    returned as new objects (immutable inputs).

    ``values_by_date`` is a list of per-date {category: dollars} mappings, one
    entry per date, aligned positionally with the caller's date list.
    """
    if len(names) <= DASHBOARD_STACKED_SERIES_LIMIT:
        return list(names), [dict(values) for values in values_by_date]

    totals: dict[str, float] = {name: 0.0 for name in names}
    for values in values_by_date:
        for name, amount in values.items():
            totals[name] += amount

    kept_count = DASHBOARD_STACKED_SERIES_LIMIT - 1
    # A real category equal to the reserved label is excluded from the kept set
    # so the synthetic bucket name never collides with (or overwrites) it; its
    # values fold into the bucket below alongside the ranked-out remainder.
    # Sort by descending total; ties fall back to the incoming (sorted) order so
    # the selection is deterministic. Precompute the incoming index once so key
    # construction stays O(n) rather than rescanning names per comparison.
    order = {name: index for index, name in enumerate(names)}
    ranked_names = sorted(
        (name for name in names if name != OTHER_STACKED_SERIES_LABEL),
        key=lambda name: (-totals[name], order[name]),
    )
    kept_names = ranked_names[:kept_count]
    kept_set = frozenset(kept_names)
    bucketed_names = kept_names + [OTHER_STACKED_SERIES_LABEL]

    bucketed_values: list[dict[str, float]] = []
    for values in values_by_date:
        point: dict[str, float] = {name: values[name] for name in kept_names}
        point[OTHER_STACKED_SERIES_LABEL] = sum(
            amount for name, amount in values.items() if name not in kept_set
        )
        bucketed_values.append(point)

    return bucketed_names, bucketed_values


def _warehouse_row_dollars(
    row: DatasetRow,
    convert: ConvertCredits,
) -> float:
    """Total estimated dollars for one warehouse_spend_daily row.

    Combines compute credits priced at WAREHOUSE_METERING/COMPUTE with the
    cloud-services credits (credits_used - credits_used_compute) priced at
    CLOUD_SERVICES. Cloud-services credits are an invariant non-negative
    quantity; we raise here rather than silently absorbing impossible negatives,
    matching the codebase's fail-loud stance on bad states. ``assert`` is avoided
    so ``python -O`` cannot strip the guard and clamp real negatives.
    """
    usage_date = _as_date(row["usage_date"])
    compute_credits = _required_float_field(
        row, "warehouse_spend_daily", "credits_used_compute"
    )
    cloud_services_credits = (
        _required_float_field(row, "warehouse_spend_daily", "credits_used")
        - compute_credits
    )
    if cloud_services_credits < -_FLOAT_EPSILON:
        raise ValueError(
            "warehouse_spend_daily credits_used must be >= credits_used_compute"
        )
    # Clamp the epsilon band (tiny negative float noise) to zero.
    cloud_services_credits = max(cloud_services_credits, 0.0)

    return convert(
        compute_credits, usage_date, "WAREHOUSE_METERING", "COMPUTE"
    ) + convert(cloud_services_credits, usage_date, "CLOUD_SERVICES", None)


def _build_warehouse_spend(
    *,
    dates: list[date],
    warehouse_rows: list[DatasetRow],
    user_rows: list[DatasetRow],
    currency: str,
    convert: ConvertCredits,
) -> WarehouseSpendViewModel:
    # Ranked warehouses and the stacked daily series both derive from the same
    # per-warehouse dollar total (compute + cloud services) so the two views can
    # never disagree.
    ranked_warehouses = _rank_named_amounts(
        [
            NamedAmount(
                name=_string_field(row, "warehouse_name", "Unknown warehouse"),
                credits=_required_float_field(
                    row, "warehouse_spend_daily", "credits_used"
                ),
                spend=_warehouse_row_dollars(row, convert),
            )
            for row in warehouse_rows
        ],
        currency,
    )
    ranked_users = _rank_named_amounts(
        [
            NamedAmount(
                name=_string_field(row, "user_name", "Unknown user"),
                credits=_required_float_field(
                    row, "query_compute_by_user_daily", "credits_attributed_compute"
                ),
                spend=convert(
                    _required_float_field(
                        row,
                        "query_compute_by_user_daily",
                        "credits_attributed_compute",
                    ),
                    _as_date(row["usage_date"]),
                    "WAREHOUSE_METERING",
                    "COMPUTE",
                ),
            )
            for row in user_rows
        ],
        currency,
    )

    warehouse_names = sorted(
        {
            _string_field(row, "warehouse_name", "Unknown warehouse")
            for row in warehouse_rows
        }
    )
    spend_by_date_and_warehouse = {
        (usage_date, warehouse_name): 0.0
        for usage_date in dates
        for warehouse_name in warehouse_names
    }
    for row in warehouse_rows:
        usage_date = _as_date(row["usage_date"])
        warehouse_name = _string_field(row, "warehouse_name", "Unknown warehouse")
        key = (usage_date, warehouse_name)
        spend_by_date_and_warehouse[key] = spend_by_date_and_warehouse.get(
            key, 0.0
        ) + _warehouse_row_dollars(row, convert)

    # Total across the window of the per-warehouse daily dollars — the same
    # derived data the stacked chart renders, so the KPI and chart can never
    # disagree. Bucketing reshapes only the stacked series, not this total.
    total = sum(spend_by_date_and_warehouse.values())

    values_by_date = [
        {
            warehouse_name: spend_by_date_and_warehouse[(usage_date, warehouse_name)]
            for warehouse_name in warehouse_names
        }
        for usage_date in dates
    ]
    bucketed_names, bucketed_values = _bucket_stacked_series(
        warehouse_names, values_by_date
    )
    daily_series = [
        WarehousePoint(date=usage_date.isoformat(), values=values)
        for usage_date, values in zip(dates, bucketed_values, strict=True)
    ]

    return WarehouseSpendViewModel(
        basis="estimated",
        total=total,
        total_label=_format_currency(total, currency),
        daily_series=daily_series,
        warehouse_names=bucketed_names,
        ranked_warehouses=ranked_warehouses,
        ranked_users=ranked_users,
        warehouse_bars=_build_ranked_bar_rows(ranked_warehouses),
        user_bars=_build_ranked_bar_rows(ranked_users),
        is_empty=len(ranked_warehouses) == 0,
    )


def _build_storage_spend(
    *,
    dates: list[date],
    rows: list[DatasetRow],
    billed_rows: list[DatasetRow],
    basis: SpendBasis,
    storage_rates: dict[date, RateIndexEntry],
    metadata: DashboardDatasetMetadata,
    currency: str,
) -> StorageSpendViewModel:
    # Per-date, per-database daily dollars. This grid drives BOTH the overall
    # estimated daily_series and the bucketed stacked series, so the KPI total,
    # the overall line, and the stacked chart can never disagree.
    database_names = sorted(
        {_string_field(row, "database_name", "Unknown database") for row in rows}
    )
    spend_by_date_and_db = {
        (usage_date, database_name): 0.0
        for usage_date in dates
        for database_name in database_names
    }
    for row in rows:
        usage_date = _as_date(row["usage_date"])
        database_name = _string_field(row, "database_name", "Unknown database")
        price = _storage_price_for(usage_date, storage_rates, metadata) or 0.0
        key = (usage_date, database_name)
        spend_by_date_and_db[key] = spend_by_date_and_db.get(
            key, 0.0
        ) + _storage_bytes_to_daily_dollars(_storage_bytes(row), price)

    total = sum(spend_by_date_and_db.values())

    # Per-database spend OVER THE SELECTED WINDOW. Summed from the same grid that
    # feeds the stacked series and the KPI total, so these values sum to `total`.
    period_spend_by_db: dict[str, float] = {}
    for (_usage_date, database_name), spend in spend_by_date_and_db.items():
        period_spend_by_db[database_name] = (
            period_spend_by_db.get(database_name, 0.0) + spend
        )

    values_by_date = [
        {
            database_name: spend_by_date_and_db[(usage_date, database_name)]
            for database_name in database_names
        }
        for usage_date in dates
    ]
    bucketed_names, bucketed_values = _bucket_stacked_series(
        database_names, values_by_date
    )
    database_daily_series = [
        StorageDatabasePoint(date=usage_date.isoformat(), values=values)
        for usage_date, values in zip(dates, bucketed_values, strict=True)
    ]

    # The overall daily_series keeps its billed-vs-estimated semantics, but the
    # estimated path now reuses the same per-date grid so its numbers agree with
    # the stacked series (rate-sheet rate + hybrid bytes).
    if basis == "billed":
        daily_series = _daily_billed_totals(dates, billed_rows, currency)
    else:
        daily_series = [
            DollarPoint(
                date=usage_date.isoformat(),
                spend=spend,
                spend_label=_format_currency(spend, currency),
            )
            for usage_date, spend in zip(
                dates,
                (sum(values.values()) for values in values_by_date),
                strict=True,
            )
        ]

    latest_date = max((_as_date(row["usage_date"]) for row in rows), default=None)
    latest_price = (
        _storage_price_for(latest_date, storage_rates, metadata)
        if latest_date is not None
        else None
    ) or 0.0
    databases = _rank_storage_rows(
        rows,
        fallback_price_per_tb_month=latest_price,
        currency=currency,
        period_spend_by_db=period_spend_by_db,
        storage_rates=storage_rates,
        metadata=metadata,
    )
    database_bars = _build_ranked_bar_rows(
        [
            RankedSpendRow(
                name=database.name,
                spend=database.monthly_spend,
                spend_label=database.monthly_spend_label,
                credits=None,
            )
            for database in databases
        ]
    )

    return StorageSpendViewModel(
        basis=basis,
        database_basis="estimated",
        total=total,
        total_label=_format_currency(total, currency),
        daily_series=daily_series,
        database_names=bucketed_names,
        database_daily_series=database_daily_series,
        databases=databases,
        database_bars=database_bars,
        is_empty=total == 0,
    )


def _build_service_spend(
    *,
    dates: list[date],
    rows: list[DatasetRow],
    basis: SpendBasis,
    currency: str,
    convert: ConvertCredits,
) -> ServiceSpendViewModel:
    service_names = sorted(
        {_string_field(row, "service_type", "Unknown service") for row in rows}
    )
    spend_by_date_and_service = {
        (usage_date, service_name): 0.0
        for usage_date in dates
        for service_name in service_names
    }
    for row in rows:
        usage_date = _as_date(row["usage_date"])
        service_name = _string_field(row, "service_type", "Unknown service")
        key = (usage_date, service_name)
        spend_by_date_and_service[key] = spend_by_date_and_service.get(
            key, 0.0
        ) + _service_spend(row, basis, convert)

    values_by_date = [
        {
            service_name: spend_by_date_and_service[(usage_date, service_name)]
            for service_name in service_names
        }
        for usage_date in dates
    ]
    bucketed_names, bucketed_values = _bucket_stacked_series(
        service_names, values_by_date
    )
    daily_series = [
        ServicePoint(date=usage_date.isoformat(), values=values)
        for usage_date, values in zip(dates, bucketed_values, strict=True)
    ]
    ranked_services = _rank_named_amounts(
        [
            NamedAmount(
                name=_string_field(row, "service_type", "Unknown service"),
                credits=_required_float_field(
                    row, "service_spend_daily", "credits_used"
                )
                if "credits_used" in row
                else 0.0,
                spend=_service_spend(row, basis, convert),
            )
            for row in rows
        ],
        currency,
    )

    return ServiceSpendViewModel(
        basis=basis,
        daily_series=daily_series,
        service_names=bucketed_names,
        ranked_services=ranked_services,
        service_bars=_build_ranked_bar_rows(ranked_services),
        is_empty=len(ranked_services) == 0,
    )


def _service_spend(
    row: DatasetRow,
    basis: SpendBasis,
    convert: ConvertCredits,
) -> float:
    if basis == "billed":
        return _required_float_field(row, "org_spend_daily", "spend")
    if basis == "estimated":
        return convert(
            _required_float_field(row, "service_spend_daily", "credits_used"),
            _as_date(row["usage_date"]),
            _string_field(row, "service_type", "Unknown service"),
            None,
        )
    return 0.0


def _rank_named_amounts(
    rows: list[NamedAmount],
    currency: str,
) -> list[RankedSpendRow]:
    by_name: dict[str, dict[str, float]] = {}
    for row in rows:
        current = by_name.setdefault(row.name, {"spend": 0.0, "credits": 0.0})
        current["spend"] += row.spend
        current["credits"] += row.credits

    return sorted(
        [
            RankedSpendRow(
                name=name,
                spend=value["spend"],
                spend_label=_format_currency(value["spend"], currency),
                credits=value["credits"],
            )
            for name, value in by_name.items()
        ],
        key=lambda row: row.spend,
        reverse=True,
    )


def _build_ranked_bar_rows(rows: list[RankedSpendRow]) -> list[RankedBarRow]:
    # Ranked bars are uncapped: every entry is rendered inside a scrollable
    # panel on the frontend, so we no longer slice the list. The frontend still
    # filters sub-cent rows from the display.
    top_spend = rows[0].spend if rows else 0

    return [
        RankedBarRow(
            **row.model_dump(),
            bar_width_percent=max(0, (row.spend / top_spend) * 100)
            if top_spend > 0
            else 0,
        )
        for row in rows
    ]


def _cap_detail_rows[T](rows: list[T]) -> list[T]:
    return rows[:DASHBOARD_DETAIL_ROW_LIMIT]


def _rank_storage_rows(
    rows: list[DatasetRow],
    *,
    fallback_price_per_tb_month: float,
    currency: str,
    period_spend_by_db: dict[str, float],
    storage_rates: dict[date, RateIndexEntry],
    metadata: DashboardDatasetMetadata,
) -> list[StorageDatabaseRow]:
    # For each database, take its OWN latest row in the window (max usage_date)
    # and size it from that row's bytes. A database that has window spend but no
    # storage row (only present in period_spend_by_db) gets bytes=0.0.
    latest_bytes_by_database: dict[str, float] = {}
    latest_date_by_database: dict[str, date] = {}
    for row in rows:
        database_name = _string_field(row, "database_name", "Unknown database")
        usage_date = _as_date(row["usage_date"])
        current_latest = latest_date_by_database.get(database_name)
        if current_latest is None or usage_date >= current_latest:
            latest_date_by_database[database_name] = usage_date
            latest_bytes_by_database[database_name] = _storage_bytes(row)

    database_names = sorted(set(latest_bytes_by_database) | set(period_spend_by_db))

    ranked_rows = [
        _build_storage_database_row(
            database_name=database_name,
            bytes_value=latest_bytes_by_database.get(database_name, 0.0),
            # Price each database from the rate in effect on ITS OWN latest date,
            # so a size snapshot is never paired with a price from a different
            # date. Databases with no storage row fall back to the global price.
            price_per_tb_month=_price_for_database_latest(
                latest_date_by_database.get(database_name),
                storage_rates,
                metadata,
                fallback_price_per_tb_month,
            ),
            period_spend=period_spend_by_db.get(database_name, 0.0),
            currency=currency,
        )
        for database_name in database_names
    ]

    return sorted(ranked_rows, key=lambda row: row.period_spend, reverse=True)


def _price_for_database_latest(
    latest_date: date | None,
    storage_rates: dict[date, RateIndexEntry],
    metadata: DashboardDatasetMetadata,
    fallback_price_per_tb_month: float,
) -> float:
    if latest_date is None:
        return fallback_price_per_tb_month
    return (
        _storage_price_for(latest_date, storage_rates, metadata)
        or fallback_price_per_tb_month
    )


def _build_storage_database_row(
    *,
    database_name: str,
    bytes_value: float,
    price_per_tb_month: float,
    period_spend: float,
    currency: str,
) -> StorageDatabaseRow:
    monthly_spend = (bytes_value / 1_000_000_000_000) * price_per_tb_month
    return StorageDatabaseRow(
        name=database_name,
        bytes=bytes_value,
        bytes_label=_format_bytes(bytes_value),
        monthly_spend=monthly_spend,
        monthly_spend_label=_format_currency(monthly_spend, currency),
        period_spend=period_spend,
        period_spend_label=_format_currency(period_spend, currency),
    )


def _build_warehouse_details(
    rows: list[DatasetRow],
    currency: str,
    convert: ConvertCredits,
) -> list[WarehouseDetailRow]:
    by_warehouse: dict[str, dict[str, float]] = {}
    for row in rows:
        warehouse_name = _string_field(row, "warehouse_name", "Unknown warehouse")
        current = by_warehouse.setdefault(
            warehouse_name,
            {"spend": 0.0, "credits_compute": 0.0, "credits_total": 0.0},
        )
        usage_date = _as_date(row["usage_date"])
        credits_compute = _required_float_field(
            row, "warehouse_spend_daily", "credits_used_compute"
        )
        current["spend"] += convert(
            credits_compute,
            usage_date,
            "WAREHOUSE_METERING",
            "COMPUTE",
        )
        current["credits_compute"] += credits_compute
        current["credits_total"] += _required_float_field(
            row, "warehouse_spend_daily", "credits_used"
        )

    return sorted(
        [
            WarehouseDetailRow(
                name=name,
                spend=value["spend"],
                spend_label=_format_currency(value["spend"], currency),
                credits=value["credits_compute"],
                credits_compute=value["credits_compute"],
                credits_total=value["credits_total"],
            )
            for name, value in by_warehouse.items()
        ],
        key=lambda row: row.spend,
        reverse=True,
    )


def _user_warehouse_label(rows: list[DatasetRow], user_name: str) -> str:
    warehouse_names = {
        _string_field(row, "warehouse_name", "Unknown warehouse")
        for row in rows
        if _string_field(row, "user_name", "Unknown user") == user_name
    }
    if len(warehouse_names) == 1:
        return next(iter(warehouse_names))
    return "Multiple warehouses"


def _is_consumption_spend_row(row: DatasetRow) -> bool:
    return row.get("billing_type") == "CONSUMPTION"


def _is_storage_spend_row(row: DatasetRow) -> bool:
    return row.get("rating_type") == "STORAGE" or row.get("service_type") == "STORAGE"


def _storage_bytes(row: DatasetRow) -> float:
    return (
        _required_float_field(row, "database_storage_daily", "average_database_bytes")
        + _nullable_float_field(
            row,
            "database_storage_daily",
            "average_failsafe_bytes",
            default=0.0,
        )
        + _nullable_float_field(
            row,
            "database_storage_daily",
            "average_hybrid_table_storage_bytes",
            default=0.0,
        )
    )


def _nullable_float_field(
    row: DatasetRow,
    dataset_key: str,
    field_name: str,
    *,
    default: float,
) -> float:
    if row.get(field_name) is None:
        return default
    return _required_float_field(row, dataset_key, field_name)


def _required_float_field(row: DatasetRow, dataset_key: str, field_name: str) -> float:
    value = row.get(field_name)
    if value is None:
        raise ValueError(
            "Dashboard dataset row is missing required numeric field "
            f"{dataset_key}.{field_name}."
        )
    if isinstance(value, bool):
        raise ValueError(
            "Dashboard dataset row has invalid numeric field "
            f"{dataset_key}.{field_name}."
        )
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Dashboard dataset row has invalid numeric field "
            f"{dataset_key}.{field_name}."
        ) from exc
    if not math.isfinite(parsed):
        raise ValueError(
            "Dashboard dataset row has invalid numeric field "
            f"{dataset_key}.{field_name}."
        )
    return parsed


def _string_field(row: DatasetRow, field_name: str, fallback: str) -> str:
    value = row.get(field_name)
    if value is None:
        return fallback
    return str(value)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from app.models import DashboardDatasetMetadata, DashboardRun
from app.services.dashboard_view_models import (
    ComputeSpendViewModel,
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
    StorageDatabaseRow,
    StorageSpendViewModel,
    TotalSpendViewModel,
    UnsupportedViewModel,
    UserDetailRow,
    WarehouseDetailRow,
)

DEFAULT_VIEW_WINDOW_DAYS = 30
SUPPORTED_VIEW_WINDOW_DAYS = frozenset({7, 30, 90})
DASHBOARD_RANKED_BAR_LIMIT = 8
DASHBOARD_DETAIL_ROW_LIMIT = 50
CURRENCY_SYMBOL_PREFIXES = {
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CAD": "CA$",
    "AUD": "A$",
}
CURRENCY_CODE_PREFIXES = frozenset({"CHF", "NOK", "SGD"})
CURRENCY_CODE_SEPARATOR = "\u00a0"

DatasetRow = dict[str, Any]
RateIndex = dict[str, "RateIndexEntry"]
ConvertCredits = Callable[[float, date, str, str | None], float]


@dataclass(frozen=True)
class DashboardRangeOutOfBoundsError(ValueError):
    source_start_date: date
    source_end_date: date

    def __str__(self) -> str:
        return "Requested dashboard range is outside stored source bounds."


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
        raise ValueError("Dashboard view accepts exactly one range mode.")
    if has_custom and (start_date is None or end_date is None):
        raise ValueError("Custom dashboard range requires start_date and end_date.")

    if not has_relative and not has_custom:
        window_days = DEFAULT_VIEW_WINDOW_DAYS
        has_relative = True

    if has_relative:
        if window_days not in SUPPORTED_VIEW_WINDOW_DAYS:
            raise ValueError("Unsupported dashboard window_days.")
        effective_start = window_start_for(through_date, int(window_days))
        effective_end = through_date
        mode = "relative"
        effective_window_days = int(window_days)
    else:
        assert start_date is not None
        assert end_date is not None
        if start_date > end_date:
            raise ValueError(
                "Custom dashboard range start_date must be on or before end_date."
            )
        if start_date > through_date:
            raise ValueError(
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
    view_range: DashboardViewRange,
    projection_range: DashboardProjectionRange,
) -> DashboardViewResponse:
    unsupported = _unsupported_view_model(metadata)
    if unsupported is not None:
        return _empty_dashboard_view(
            run=run,
            view_range=view_range,
            projection_range=projection_range,
            header=header,
            currency=currency,
            unsupported=unsupported,
        )

    rates = _build_rate_index(_dataset_rows(datasets, "rate_sheet_daily"))
    is_billed = metadata.data_mode in {"billed", "demo"}
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
    compute_spend = _build_compute_spend(
        dates=dates,
        warehouse_rows=warehouse_rows,
        user_rows=user_rows,
        currency=currency,
        convert=convert,
    )
    storage_spend = _build_storage_spend(
        dates=dates,
        rows=storage_rows,
        billed_rows=billed_storage_rows,
        basis=basis,
        price_per_tb_month=metadata.storage_price_usd_per_tb_month,
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

    return DashboardViewResponse(
        run=run,
        range=view_range,
        projection_range=projection_range,
        header=header,
        unsupported=None,
        total_spend=total_spend,
        compute_spend=compute_spend,
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
                    for row in compute_spend.ranked_users
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
    if resolved_currency == "USD":
        amount = f"{abs(value):,.2f}"
        if value < 0:
            return f"-${amount}"
        return f"${amount}"
    if resolved_currency in CURRENCY_SYMBOL_PREFIXES:
        amount = (
            _format_compact_amount(abs(value))
            if resolved_currency == "JPY"
            else f"{abs(value):,.2f}"
        )
        sign = "-" if value < 0 else ""
        return f"{sign}{CURRENCY_SYMBOL_PREFIXES[resolved_currency]}{amount}"
    if resolved_currency in CURRENCY_CODE_PREFIXES:
        amount = f"{abs(value):,.2f}"
        sign = "-" if value < 0 else ""
        return f"{sign}{resolved_currency}{CURRENCY_CODE_SEPARATOR}{amount}"
    amount = f"{value:,.2f}"
    return f"{amount} {resolved_currency}"


def _format_compact_amount(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _format_usage_date(value: date) -> str:
    return f"{value:%b} {value.day}, {value.year}"


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
    if metadata.unsupported_reason != "mixed_currency":
        return None
    return UnsupportedViewModel(
        title="Mixed currencies are not supported",
        detail="Select an account with a single billing currency to view spend.",
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
        total_spend=_empty_total_spend(currency),
        compute_spend=_empty_compute_spend(),
        storage_spend=_empty_storage_spend(),
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


def _empty_compute_spend() -> ComputeSpendViewModel:
    return ComputeSpendViewModel(
        compute_basis="estimated",
        daily_series=[],
        ranked_warehouses=[],
        ranked_users=[],
        warehouse_bars=[],
        user_bars=[],
        is_empty=True,
    )


def _empty_storage_spend() -> StorageSpendViewModel:
    return StorageSpendViewModel(
        basis="estimated",
        database_basis="estimated",
        daily_series=[],
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
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _dataset_rows(
    datasets: dict[str, list[DatasetRow]], dataset_key: str
) -> list[DatasetRow]:
    return datasets.get(dataset_key, [])


def _build_rate_index(rows: list[DatasetRow]) -> RateIndex:
    rates: RateIndex = {}
    for row in rows:
        usage_date = _as_date(row["usage_date"])
        service_type = _string_field(row, "service_type", "Unknown service")
        rating_type = _optional_string(row.get("rating_type"))
        entry = RateIndexEntry(
            currency=_optional_string(row.get("currency")),
            effective_rate=_required_float_field(
                row, "rate_sheet_daily", "effective_rate"
            ),
        )

        if rating_type is not None:
            rates[_rate_key(usage_date, service_type, rating_type)] = entry

        service_only_key = _rate_key(usage_date, service_type)
        if service_only_key not in rates or rating_type == "COMPUTE":
            rates[service_only_key] = entry
    return rates


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


def _build_compute_spend(
    *,
    dates: list[date],
    warehouse_rows: list[DatasetRow],
    user_rows: list[DatasetRow],
    currency: str,
    convert: ConvertCredits,
) -> ComputeSpendViewModel:
    ranked_warehouses = _rank_named_amounts(
        [
            NamedAmount(
                name=_string_field(row, "warehouse_name", "Unknown warehouse"),
                credits=_required_float_field(
                    row, "warehouse_spend_daily", "credits_used_compute"
                ),
                spend=convert(
                    _required_float_field(
                        row, "warehouse_spend_daily", "credits_used_compute"
                    ),
                    _as_date(row["usage_date"]),
                    "WAREHOUSE_METERING",
                    "COMPUTE",
                ),
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
    spend_by_date = {usage_date: 0.0 for usage_date in dates}
    for row in warehouse_rows:
        usage_date = _as_date(row["usage_date"])
        spend_by_date[usage_date] = spend_by_date.get(usage_date, 0.0) + convert(
            _required_float_field(row, "warehouse_spend_daily", "credits_used_compute"),
            usage_date,
            "WAREHOUSE_METERING",
            "COMPUTE",
        )
    daily_series = [
        DollarPoint(
            date=usage_date.isoformat(),
            spend=spend_by_date[usage_date],
            spend_label=_format_currency(spend_by_date[usage_date], currency),
        )
        for usage_date in dates
    ]

    return ComputeSpendViewModel(
        compute_basis="estimated",
        daily_series=daily_series,
        ranked_warehouses=ranked_warehouses,
        ranked_users=ranked_users,
        warehouse_bars=_build_ranked_bar_rows(ranked_warehouses),
        user_bars=_build_ranked_bar_rows(ranked_users),
        is_empty=all(row.spend == 0 for row in daily_series),
    )


def _build_storage_spend(
    *,
    dates: list[date],
    rows: list[DatasetRow],
    billed_rows: list[DatasetRow],
    basis: SpendBasis,
    price_per_tb_month: float,
    currency: str,
) -> StorageSpendViewModel:
    daily_series = (
        _daily_billed_totals(dates, billed_rows, currency)
        if basis == "billed"
        else _daily_storage_estimates(dates, rows, price_per_tb_month, currency)
    )
    latest_date = max((_as_date(row["usage_date"]) for row in rows), default=None)
    databases = _rank_storage_rows(
        [
            row
            for row in rows
            if latest_date and _as_date(row["usage_date"]) == latest_date
        ],
        price_per_tb_month,
        currency,
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
        daily_series=daily_series,
        databases=databases,
        database_bars=database_bars,
        is_empty=all(row.spend == 0 for row in daily_series),
    )


def _daily_storage_estimates(
    dates: list[date],
    rows: list[DatasetRow],
    price_per_tb_month: float,
    currency: str,
) -> list[DollarPoint]:
    spend_by_date = {usage_date: 0.0 for usage_date in dates}
    for row in rows:
        usage_date = _as_date(row["usage_date"])
        bytes_value = _storage_bytes(row)
        spend_by_date[usage_date] = spend_by_date.get(
            usage_date, 0.0
        ) + _storage_bytes_to_daily_dollars(bytes_value, price_per_tb_month)

    return [
        DollarPoint(
            date=usage_date.isoformat(),
            spend=spend_by_date[usage_date],
            spend_label=_format_currency(spend_by_date[usage_date], currency),
        )
        for usage_date in dates
    ]


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

    daily_series = [
        ServicePoint(
            date=usage_date.isoformat(),
            values={
                service_name: spend_by_date_and_service[(usage_date, service_name)]
                for service_name in service_names
            },
        )
        for usage_date in dates
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
        service_names=service_names,
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
    shown = rows[:DASHBOARD_RANKED_BAR_LIMIT]
    top_spend = shown[0].spend if shown else 0

    return [
        RankedBarRow(
            **row.model_dump(),
            bar_width_percent=max(0, (row.spend / top_spend) * 100)
            if top_spend > 0
            else 0,
        )
        for row in shown
    ]


def _cap_detail_rows[T](rows: list[T]) -> list[T]:
    return rows[:DASHBOARD_DETAIL_ROW_LIMIT]


def _rank_storage_rows(
    rows: list[DatasetRow],
    price_per_tb_month: float,
    currency: str,
) -> list[StorageDatabaseRow]:
    by_database: dict[str, float] = {}
    for row in rows:
        database_name = _string_field(row, "database_name", "Unknown database")
        by_database[database_name] = by_database.get(
            database_name, 0.0
        ) + _storage_bytes(row)

    ranked_rows = []
    for database_name, bytes_value in by_database.items():
        monthly_spend = (bytes_value / 1_000_000_000_000) * price_per_tb_month
        ranked_rows.append(
            StorageDatabaseRow(
                name=database_name,
                bytes=bytes_value,
                monthly_spend=monthly_spend,
                monthly_spend_label=_format_currency(monthly_spend, currency),
            )
        )

    return sorted(ranked_rows, key=lambda row: row.monthly_spend, reverse=True)


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
    return _required_float_field(
        row, "database_storage_daily", "average_database_bytes"
    ) + _required_float_field(row, "database_storage_daily", "average_failsafe_bytes")


def _required_float_field(row: DatasetRow, dataset_key: str, field_name: str) -> float:
    value = row.get(field_name)
    if value is None:
        raise ValueError(
            "Dashboard dataset row is missing required numeric field "
            f"{dataset_key}.{field_name}."
        )
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Dashboard dataset row has invalid numeric field "
            f"{dataset_key}.{field_name}."
        ) from exc


def _string_field(row: DatasetRow, field_name: str, fallback: str) -> str:
    value = row.get(field_name)
    if value is None:
        return fallback
    return str(value)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)

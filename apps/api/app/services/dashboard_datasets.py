from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable

from pydantic import BaseModel

from app.config import Settings
from app.models import DashboardDatasetMetadata, SourceAvailability
from app.services.cost_metrics import (
    build_dashboard_summary,
    derive_account_spend_daily,
)
from app.services.dashboard_registry import DashboardSource, load_dashboard_registry
from app.services.dataset_bounds import bound_user_compute_rows
from app.services.snowflake_client import (
    SnowflakeConfigurationError,
    SnowflakeConnectionConfig,
    SnowflakeQueryError,
    execute_source_query,
)

FETCH_WINDOW_DAYS = 100
_ACCOUNT_LOCATOR_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,64}$")
OPTIONAL_ORG_SOURCE_IDS = frozenset({"capacity_balance_daily"})

ExecuteFn = Callable[[str, dict[str, Any]], list[dict[str, Any]]]


class DashboardSourcesUnavailableError(RuntimeError):
    pass


class SnowflakeDashboardData(BaseModel):
    datasets: dict[str, list[dict[str, Any]]]
    metadata: DashboardDatasetMetadata
    summary: dict[str, Any]


def build_snowflake_dashboard_data(
    settings: Settings,
    execute: ExecuteFn | None = None,
    summary_window_days: int = FETCH_WINDOW_DAYS,
    connection_config: SnowflakeConnectionConfig | None = None,
) -> SnowflakeDashboardData:
    if execute is not None:
        execute_source = execute
    else:

        def execute_source(
            sql: str, bind_params: dict[str, Any]
        ) -> list[dict[str, Any]]:
            return execute_source_query(sql, bind_params, connection_config)

    registry = load_dashboard_registry()
    account_sources = _sources_by_kind(registry.sources, "snowflake_account_usage")
    all_org_sources = _sources_by_kind(registry.sources, "snowflake_organization_usage")
    optional_org_sources = {
        key: source
        for key, source in all_org_sources.items()
        if key in OPTIONAL_ORG_SOURCE_IDS
    }
    org_sources = {
        key: source
        for key, source in all_org_sources.items()
        if key not in OPTIONAL_ORG_SOURCE_IDS
    }

    account_locator, locator_error = _derive_account_locator(
        registry.sources["current_account"], execute_source
    )
    org_bind_params = {
        "window_days": FETCH_WINDOW_DAYS,
        "account_locator": account_locator,
    }
    org_datasets, org_availability = _fetch_source_group(
        org_sources,
        execute_source,
        bind_params=org_bind_params,
        skip=account_locator is None,
        skip_detail=locator_error,
        unavailable_detail="Could not query Snowflake Organization Usage data.",
    )
    capacity_datasets, _capacity_availability = _fetch_source_group(
        optional_org_sources,
        execute_source,
        bind_params=org_bind_params,
        skip=account_locator is None,
        skip_detail=locator_error,
        unavailable_detail="Could not query Snowflake capacity balance data.",
    )
    account_datasets, account_availability = _fetch_source_group(
        account_sources,
        execute_source,
        bind_params={"window_days": FETCH_WINDOW_DAYS},
        unavailable_detail="Could not query Snowflake Account Usage data.",
    )

    if not org_availability.available and not account_availability.available:
        raise DashboardSourcesUnavailableError(
            "Could not query Snowflake billing or Account Usage data."
        )

    if account_availability.available:
        account_datasets["query_compute_by_user_daily"] = bound_user_compute_rows(
            account_datasets["query_compute_by_user_daily"]
        )
        account_datasets["account_spend_daily"] = derive_account_spend_daily(
            account_datasets["service_spend_daily"]
        )
        account_datasets["top_warehouses_table"] = build_top_warehouses_table(
            account_datasets["warehouse_spend_daily"]
        )
    else:
        account_datasets["account_spend_daily"] = []
        account_datasets["top_warehouses_table"] = []

    current_account = (
        [{"account_locator": account_locator}] if account_locator is not None else []
    )
    datasets = {
        **account_datasets,
        **org_datasets,
        **capacity_datasets,
        "current_account": current_account,
    }
    metadata = _build_metadata(
        settings=settings,
        datasets=datasets,
        account_locator=account_locator,
        org_availability=org_availability,
        account_availability=account_availability,
    )
    summary_current_usage_date = (
        metadata.account_usage_through_date
        or metadata.billing_through_date
        or date.today()
    ) + timedelta(days=1)
    summary = build_dashboard_summary(
        account_spend_daily=datasets["account_spend_daily"],
        warehouse_spend_daily=datasets["warehouse_spend_daily"],
        database_storage_daily=datasets["database_storage_daily"],
        current_usage_date=summary_current_usage_date,
        window_days=summary_window_days,
        storage_price_usd_per_tb_month=settings.storage_price_usd_per_tb_month,
    ).model_dump(mode="json")

    return SnowflakeDashboardData(
        datasets={
            dataset_key: _json_ready_rows(rows)
            for dataset_key, rows in datasets.items()
        },
        metadata=metadata,
        summary=summary,
    )


def build_top_warehouses_table(
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
            credits_by_warehouse.items(), key=lambda item: item[1], reverse=True
        )[:10]
    ]


def _sources_by_kind(
    sources: dict[str, DashboardSource], kind: str
) -> dict[str, DashboardSource]:
    return {
        dataset_key: source
        for dataset_key, source in sources.items()
        if source.kind == kind
    }


def _derive_account_locator(
    current_account_source: DashboardSource,
    execute: ExecuteFn,
) -> tuple[str | None, str | None]:
    try:
        rows = execute(current_account_source.sql, {})
    except (SnowflakeQueryError, SnowflakeConfigurationError):
        return None, "Could not determine Snowflake account."

    if not rows or not rows[0].get("account_locator"):
        return None, "Could not determine Snowflake account."
    account_locator = str(rows[0]["account_locator"])
    if not _ACCOUNT_LOCATOR_PATTERN.fullmatch(account_locator):
        return None, "Could not determine Snowflake account."
    return account_locator, None


def _fetch_source_group(
    sources: dict[str, DashboardSource],
    execute: ExecuteFn,
    *,
    bind_params: dict[str, Any],
    unavailable_detail: str,
    skip: bool = False,
    skip_detail: str | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], SourceAvailability]:
    empty = {dataset_key: [] for dataset_key in sources}
    if skip:
        return empty, SourceAvailability(available=False, detail=skip_detail)

    datasets: dict[str, list[dict[str, Any]]] = {}
    try:
        for dataset_key, source in sources.items():
            datasets[dataset_key] = execute(source.sql, bind_params)
    except (SnowflakeQueryError, SnowflakeConfigurationError):
        return empty, SourceAvailability(available=False, detail=unavailable_detail)

    return datasets, SourceAvailability(available=True)


def _build_metadata(
    *,
    settings: Settings,
    datasets: dict[str, list[dict[str, Any]]],
    account_locator: str | None,
    org_availability: SourceAvailability,
    account_availability: SourceAvailability,
) -> DashboardDatasetMetadata:
    org_currencies = {
        str(row["currency"])
        for row in datasets["org_spend_daily"]
        if row.get("currency") is not None
    }
    unsupported_reason = "mixed_currency" if len(org_currencies) > 1 else None
    data_mode = "billed" if org_availability.available else "estimated"
    currency = (
        None if unsupported_reason else _currency_for_mode(data_mode, org_currencies)
    )

    return DashboardDatasetMetadata(
        data_mode=data_mode,
        account_locator=account_locator,
        currency=currency,
        billing_through_date=_max_usage_date(datasets["org_spend_daily"])
        if org_availability.available
        else None,
        account_usage_through_date=_max_usage_date(
            [
                *datasets["warehouse_spend_daily"],
                *datasets["service_spend_daily"],
                *datasets["query_compute_by_user_daily"],
                *datasets["database_storage_daily"],
            ]
        )
        if account_availability.available
        else None,
        estimated_credit_price_usd=settings.estimated_credit_price_usd,
        storage_price_usd_per_tb_month=settings.storage_price_usd_per_tb_month,
        unsupported_reason=unsupported_reason,
        organization_usage=org_availability,
        account_usage=account_availability,
    )


def _currency_for_mode(data_mode: str, org_currencies: set[str]) -> str:
    if data_mode == "billed" and len(org_currencies) == 1:
        return next(iter(org_currencies))
    return "USD"


def _max_usage_date(rows: list[dict[str, Any]]) -> date | None:
    usage_dates = [_as_date(row["usage_date"]) for row in rows if row.get("usage_date")]
    return max(usage_dates) if usage_dates else None


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _json_ready_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: _json_ready_value(value)
            for key, value in (
                row.model_dump().items() if isinstance(row, BaseModel) else row.items()
            )
        }
        for row in rows
    ]


def _json_ready_value(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Snowflake numeric value must be finite.")
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value

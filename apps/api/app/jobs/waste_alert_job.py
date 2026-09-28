"""Scheduled entry point for the waste alert digest.

Invoked externally (cron, Cloud Scheduler, GitHub Actions). Greysight
does not embed a scheduler.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx

from app.config import Settings
from app.models import DashboardDatasetMetadata
from app.services.dashboard_datasets import build_snowflake_dashboard_data
from app.services.dashboard_view_builder import (
    ConvertCredits,
    DatasetRow,
    _build_rate_index,
    _credits_to_dollars,
    _dataset_rows,
    _rows_in_window,
)
from app.services.dashboard_view_models import DashboardViewRange
from app.services.demo_data import build_demo_dashboard_dataset
from app.services.store import Store, get_store
from app.services.waste_alert_delivery import deliver_digest
from app.services.waste_alerts import (
    build_waste_alert_digest,
    record_alert_state,
)


def load_dashboard_datasets(
    settings: Settings,
) -> tuple[dict[str, list[DatasetRow]], DashboardDatasetMetadata]:
    """Load the same warehouse/rate datasets the dashboard uses.

    Demo mode uses ``build_demo_dashboard_dataset``. Live mode uses
    ``build_snowflake_dashboard_data`` (approved registry SQL only).
    """
    if settings.data_source == "demo":
        payload = build_demo_dashboard_dataset()
        return payload.datasets, payload.metadata
    data = build_snowflake_dashboard_data(settings)
    return data.datasets, data.metadata


def build_convert_from_datasets(
    datasets: dict[str, list[DatasetRow]],
    metadata: DashboardDatasetMetadata,
) -> ConvertCredits:
    rates = _build_rate_index(_dataset_rows(datasets, "rate_sheet_daily"))

    def convert(
        credits: float,
        usage_date: date,
        service_type: str,
        rating_type: str | None = None,
    ) -> float:
        dollars = _credits_to_dollars(
            credits=credits,
            usage_date=usage_date,
            service_type=service_type,
            rates=rates,
            metadata=metadata,
            rating_type=rating_type,
        )
        return dollars or 0.0

    return convert


async def run_waste_alert_job(
    now: datetime | None = None,
    *,
    settings: Settings | None = None,
    store: Store | None = None,
    warehouse_rows: list[DatasetRow] | None = None,
    convert: ConvertCredits | None = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Compute one digest and, if non-empty, deliver + persist state.

    Safe to call unconditionally: disabled flag makes it a no-op.
    Optional ``store`` / ``warehouse_rows`` / ``convert`` / ``client``
    let tests skip Snowflake and the real webhook.
    """
    resolved = settings or Settings()
    if not resolved.waste_alert_enabled:
        return

    webhook_url = resolved.waste_alert_webhook_url
    if not webhook_url:
        raise RuntimeError(
            "WASTE_ALERT_ENABLED is true but WASTE_ALERT_WEBHOOK_URL is unset"
        )

    now = now or datetime.now(timezone.utc)
    end_date = now.date()
    start_date = end_date - timedelta(days=resolved.waste_alert_window_days - 1)
    view_range = DashboardViewRange(
        mode="custom",
        window_days=None,
        start_date=start_date,
        end_date=end_date,
    )
    resolved_store = store or get_store(resolved)

    if warehouse_rows is None or convert is None:
        datasets, metadata = load_dashboard_datasets(resolved)
        convert = convert or build_convert_from_datasets(datasets, metadata)
        warehouse_rows = _rows_in_window(
            _dataset_rows(datasets, "warehouse_spend_daily"),
            view_range.start_date,
            view_range.end_date,
        )

    digest = await build_waste_alert_digest(
        warehouse_rows=warehouse_rows,
        basis="estimated",
        currency=resolved.waste_alert_currency,
        convert=convert,
        view_range=view_range,
        threshold_usd=resolved.waste_alert_monthly_threshold_usd,
        cooldown=timedelta(hours=resolved.waste_alert_cooldown_hours),
        store=resolved_store,
        now=now,
    )

    if digest.is_empty:
        return

    await deliver_digest(digest, webhook_url, client=client)
    await record_alert_state(digest, resolved_store)


if __name__ == "__main__":
    asyncio.run(run_waste_alert_job())

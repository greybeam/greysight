"""Build the waste alert digest for a scheduled run.

Reuses `_build_warehouse_waste` from the dashboard view builder so alerts
and the UI can never disagree on projected monthly idle spend. Threshold
and cooldown-based dedup filter the model's rows down to the items that
should actually ship.

`now` is injected — never call `datetime.utcnow()` inside these functions
or the cooldown boundary becomes untestable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.waste_alert_state import WasteAlertState
from app.services.dashboard_view_builder import (
    ConvertCredits,
    DatasetRow,
    _build_warehouse_waste,
)
from app.services.dashboard_view_models import (
    DashboardViewRange,
    SpendBasis,
    WarehouseWasteRow,
)
from app.services.store import Store


@dataclass(frozen=True)
class WasteAlertItem:
    warehouse_name: str
    idle_pct: float | None
    period_idle_spend: float
    projected_monthly_idle_spend: float
    period_idle_spend_label: str
    projected_monthly_idle_spend_label: str


@dataclass(frozen=True)
class WasteAlertDigest:
    generated_at: datetime
    window_days: int
    threshold_usd: float
    items: list[WasteAlertItem]

    @property
    def is_empty(self) -> bool:
        return not self.items


async def build_waste_alert_digest(
    *,
    warehouse_rows: list[DatasetRow],
    basis: SpendBasis,
    currency: str,
    convert: ConvertCredits,
    view_range: DashboardViewRange,
    threshold_usd: float,
    cooldown: timedelta,
    store: Store,
    now: datetime,
) -> WasteAlertDigest:
    """Compute the digest for a single run.

    Filters the shared waste model's rows by:
      1. `projected_monthly_idle_spend >= threshold_usd`
      2. no prior alert within `cooldown`
    Items surviving both checks are what will be delivered — and only
    those items get their state recorded by `record_alert_state`.
    """
    model = _build_warehouse_waste(
        warehouse_rows=warehouse_rows,
        basis=basis,
        currency=currency,
        convert=convert,
        view_range=view_range,
        row_limit=None,
    )

    items: list[WasteAlertItem] = []
    for row in model.rows:
        if row.projected_monthly_idle_spend < threshold_usd:
            continue
        prior = await store.get_waste_alert_state(row.name)
        if prior is not None and (now - prior.last_alerted_at) < cooldown:
            continue
        items.append(_item_from_row(row))

    window_days = (view_range.end_date - view_range.start_date).days + 1

    return WasteAlertDigest(
        generated_at=now,
        window_days=window_days,
        threshold_usd=threshold_usd,
        items=items,
    )


async def record_alert_state(digest: WasteAlertDigest, store: Store) -> None:
    """Persist dedup state for exactly the items that shipped.

    Filtered-out items (below threshold, or within cooldown) are NEVER
    written — writing them would either mark a fine warehouse as
    "recently alerted" (silencing future real alerts) or double-write
    a warehouse that was suppressed by cooldown (extending its
    silence past what the operator configured).
    """
    for item in digest.items:
        await store.set_waste_alert_state(
            WasteAlertState(
                warehouse_name=item.warehouse_name,
                last_alerted_at=digest.generated_at,
                last_projected_monthly_idle_spend=item.projected_monthly_idle_spend,
            )
        )


def _item_from_row(row: WarehouseWasteRow) -> WasteAlertItem:
    return WasteAlertItem(
        warehouse_name=row.name,
        idle_pct=row.idle_pct,
        period_idle_spend=row.period_idle_spend,
        projected_monthly_idle_spend=row.projected_monthly_idle_spend,
        period_idle_spend_label=row.period_idle_spend_label,
        projected_monthly_idle_spend_label=row.projected_monthly_idle_spend_label,
    )

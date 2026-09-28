from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.waste_alert_state import WasteAlertState
from app.services.dashboard_view_models import DashboardViewRange
from app.services.store_inmemory import InMemoryStore
from app.services.waste_alerts import (
    build_waste_alert_digest,
    record_alert_state,
)


def _convert(credits, usage_date, service_type, rating_type=None):
    del usage_date, service_type, rating_type
    return credits * 2.0  # $2 per credit — matches PR #71's test fixture


def _view_range(days: int = 10) -> DashboardViewRange:
    return DashboardViewRange(
        mode="custom",
        window_days=None,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 1) + timedelta(days=days - 1),
    )


def _row(name: str, compute: float, attributed: float | None) -> dict:
    return {
        "usage_date": date(2026, 6, 1),
        "warehouse_name": name,
        "credits_used": compute,
        "credits_used_compute": compute,
        "credits_attributed_queries": attributed,
    }


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


@pytest.mark.anyio
async def test_below_threshold_warehouse_excluded():
    store = InMemoryStore()
    # 5 idle credits * $2 = $10 period; 10-day window -> $30 monthly.
    digest = await build_waste_alert_digest(
        warehouse_rows=[_row("SMALL", compute=10.0, attributed=5.0)],
        basis="estimated",
        currency="USD",
        convert=_convert,
        view_range=_view_range(10),
        threshold_usd=100.0,
        cooldown=timedelta(hours=168),
        store=store,
        now=NOW,
    )
    assert digest.is_empty is True


@pytest.mark.anyio
async def test_above_threshold_warehouse_included():
    store = InMemoryStore()
    # 100 idle credits * $2 = $200 period; 10-day window -> $600 monthly.
    digest = await build_waste_alert_digest(
        warehouse_rows=[_row("BIG", compute=100.0, attributed=0.0)],
        basis="estimated",
        currency="USD",
        convert=_convert,
        view_range=_view_range(10),
        threshold_usd=100.0,
        cooldown=timedelta(hours=168),
        store=store,
        now=NOW,
    )
    assert [item.warehouse_name for item in digest.items] == ["BIG"]
    assert digest.items[0].projected_monthly_idle_spend == pytest.approx(600.0)


@pytest.mark.anyio
async def test_warehouse_within_cooldown_excluded():
    store = InMemoryStore()
    await store.set_waste_alert_state(
        WasteAlertState(
            warehouse_name="BIG",
            last_alerted_at=NOW - timedelta(hours=100),
            last_projected_monthly_idle_spend=600.0,
        )
    )
    digest = await build_waste_alert_digest(
        warehouse_rows=[_row("BIG", compute=100.0, attributed=0.0)],
        basis="estimated",
        currency="USD",
        convert=_convert,
        view_range=_view_range(10),
        threshold_usd=100.0,
        cooldown=timedelta(hours=168),
        store=store,
        now=NOW,
    )
    assert digest.is_empty is True


@pytest.mark.anyio
async def test_warehouse_past_cooldown_re_alerts():
    store = InMemoryStore()
    await store.set_waste_alert_state(
        WasteAlertState(
            warehouse_name="BIG",
            last_alerted_at=NOW - timedelta(hours=200),
            last_projected_monthly_idle_spend=600.0,
        )
    )
    digest = await build_waste_alert_digest(
        warehouse_rows=[_row("BIG", compute=100.0, attributed=0.0)],
        basis="estimated",
        currency="USD",
        convert=_convert,
        view_range=_view_range(10),
        threshold_usd=100.0,
        cooldown=timedelta(hours=168),
        store=store,
        now=NOW,
    )
    assert [item.warehouse_name for item in digest.items] == ["BIG"]


@pytest.mark.anyio
async def test_record_alert_state_writes_only_delivered_items():
    store = InMemoryStore()
    digest = await build_waste_alert_digest(
        warehouse_rows=[
            _row("BIG", compute=100.0, attributed=0.0),
            _row("SMALL", compute=10.0, attributed=5.0),
        ],
        basis="estimated",
        currency="USD",
        convert=_convert,
        view_range=_view_range(10),
        threshold_usd=100.0,
        cooldown=timedelta(hours=168),
        store=store,
        now=NOW,
    )
    await record_alert_state(digest, store)

    assert await store.get_waste_alert_state("BIG") is not None
    assert await store.get_waste_alert_state("SMALL") is None


@pytest.mark.anyio
async def test_null_attribution_warehouse_with_zero_projected_excluded():
    """A null-attribution warehouse gets projected_monthly_idle_spend=0
    from `_build_warehouse_waste`; it should never meet the threshold."""
    store = InMemoryStore()
    digest = await build_waste_alert_digest(
        warehouse_rows=[_row("ADAPT", compute=100.0, attributed=None)],
        basis="estimated",
        currency="USD",
        convert=_convert,
        view_range=_view_range(10),
        threshold_usd=1.0,
        cooldown=timedelta(hours=168),
        store=store,
        now=NOW,
    )
    assert digest.is_empty is True

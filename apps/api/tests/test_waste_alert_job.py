"""End-to-end wiring: enabled + non-empty datasets -> one delivery + state persisted.

Uses ``httpx.MockTransport`` and ``InMemoryStore`` — no Snowflake, no webhook.
Dataset loaders are ``load_dashboard_datasets`` (demo/snowflake) and
``build_convert_from_datasets`` in ``app.jobs.waste_alert_job``.
"""
from datetime import date, datetime, timezone

import httpx
import pytest

from app.config import Settings
from app.jobs.waste_alert_job import run_waste_alert_job
from app.services.store_inmemory import InMemoryStore


def _convert(credits, usage_date, service_type, rating_type=None):
    del usage_date, service_type, rating_type
    return credits * 2.0


@pytest.mark.anyio
async def test_disabled_job_is_noop():
    store = InMemoryStore()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    settings = Settings(
        waste_alert_enabled=False,
        waste_alert_webhook_url="https://hooks.example/webhook",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await run_waste_alert_job(
            now=datetime(2026, 6, 1, tzinfo=timezone.utc),
            settings=settings,
            store=store,
            warehouse_rows=[
                {
                    "usage_date": date(2026, 6, 1),
                    "warehouse_name": "BIG",
                    "credits_used": 100.0,
                    "credits_used_compute": 100.0,
                    "credits_attributed_queries": 0.0,
                }
            ],
            convert=_convert,
            client=client,
        )

    assert calls == []
    assert await store.get_waste_alert_state("BIG") is None


@pytest.mark.anyio
async def test_enabled_job_delivers_and_records_state():
    store = InMemoryStore()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    settings = Settings(
        waste_alert_enabled=True,
        waste_alert_webhook_url="https://hooks.example/webhook",
        waste_alert_monthly_threshold_usd=100.0,
        waste_alert_window_days=1,
        waste_alert_cooldown_hours=168,
        waste_alert_currency="USD",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await run_waste_alert_job(
            now=datetime(2026, 6, 1, tzinfo=timezone.utc),
            settings=settings,
            store=store,
            warehouse_rows=[
                {
                    "usage_date": date(2026, 6, 1),
                    "warehouse_name": "BIG",
                    "credits_used": 100.0,
                    "credits_used_compute": 100.0,
                    "credits_attributed_queries": 0.0,
                }
            ],
            convert=_convert,
            client=client,
        )

    assert len(calls) == 1
    recorded = await store.get_waste_alert_state("BIG")
    assert recorded is not None
    assert recorded.last_projected_monthly_idle_spend == pytest.approx(6000.0)


@pytest.mark.anyio
async def test_enabled_without_webhook_raises():
    settings = Settings(waste_alert_enabled=True, waste_alert_webhook_url=None)
    with pytest.raises(RuntimeError, match="WASTE_ALERT_WEBHOOK_URL"):
        await run_waste_alert_job(settings=settings, store=InMemoryStore())

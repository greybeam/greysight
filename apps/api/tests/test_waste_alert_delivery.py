from datetime import datetime, timezone

import httpx
import pytest

from app.services.waste_alert_delivery import (
    WasteAlertDeliveryError,
    build_slack_payload,
    deliver_digest,
)
from app.services.waste_alerts import WasteAlertDigest, WasteAlertItem


def _digest(items=None) -> WasteAlertDigest:
    return WasteAlertDigest(
        generated_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
        window_days=14,
        threshold_usd=100.0,
        items=items or [],
    )


def _item(name: str = "BIG") -> WasteAlertItem:
    return WasteAlertItem(
        warehouse_name=name,
        idle_pct=0.75,
        period_idle_spend=200.0,
        projected_monthly_idle_spend=600.0,
        period_idle_spend_label="$200.00",
        projected_monthly_idle_spend_label="$600.00",
    )


@pytest.mark.anyio
async def test_empty_digest_short_circuits_no_http_call():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await deliver_digest(_digest(), "https://hooks.example/webhook", client=client)

    assert calls == []


def test_slack_payload_shape_includes_count_threshold_and_names():
    digest = _digest(items=[_item("BIG"), _item("MEDIUM")])
    payload = build_slack_payload(digest)

    assert "2 warehouse(s)" in payload["text"]
    assert "$100" in payload["text"]
    combined = " ".join(
        block["text"]["text"]
        for block in payload["blocks"]
        if block["type"] == "section"
    )
    assert "BIG" in combined
    assert "MEDIUM" in combined


@pytest.mark.anyio
async def test_retry_on_5xx_then_success():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(503)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await deliver_digest(
            _digest(items=[_item()]),
            "https://hooks.example/webhook",
            client=client,
            backoff_seconds=0.0,
        )

    assert attempts["count"] == 2


@pytest.mark.anyio
async def test_raises_after_max_attempts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(WasteAlertDeliveryError):
            await deliver_digest(
                _digest(items=[_item()]),
                "https://hooks.example/webhook",
                client=client,
                max_attempts=2,
                backoff_seconds=0.0,
            )


def test_null_idle_pct_renders_unavailable_not_zero_percent():
    digest = _digest(
        items=[
            WasteAlertItem(
                warehouse_name="ADAPT",
                idle_pct=None,
                period_idle_spend=0.0,
                projected_monthly_idle_spend=200.0,
                period_idle_spend_label="$0.00",
                projected_monthly_idle_spend_label="$200.00",
            )
        ]
    )
    payload = build_slack_payload(digest)
    combined = " ".join(
        block["text"]["text"]
        for block in payload["blocks"]
        if block["type"] == "section"
    )
    assert "idle share unavailable" in combined
    assert "0%" not in combined

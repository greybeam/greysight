"""Deliver a waste alert digest to a Slack-compatible webhook.

Payload uses Slack Block Kit but the transport is a plain webhook POST,
so any endpoint that accepts Slack's shape (Slack, Mattermost, Discord's
Slack-compat mode, or a custom bridge) will work.

Empty digests short-circuit before the HTTP call: silence is the correct
signal when no warehouse crosses the threshold.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.services.waste_alerts import WasteAlertDigest

logger = logging.getLogger(__name__)


class WasteAlertDeliveryError(RuntimeError):
    """Raised when the webhook POST fails after all retry attempts."""


def build_slack_payload(digest: WasteAlertDigest) -> dict[str, Any]:
    """Compose the Slack Block Kit body for a non-empty digest.

    Callers must ensure `digest.is_empty is False` — this function does
    not defend against empty input because the delivery function
    short-circuits earlier.
    """
    header = (
        f":moneybag: Greysight waste alert — "
        f"{len(digest.items)} warehouse(s) over "
        f"${digest.threshold_usd:,.0f}/mo projected idle spend "
        f"(last {digest.window_days}d)"
    )

    line_items: list[str] = []
    for item in digest.items:
        idle_fragment = (
            f", idle {round((item.idle_pct or 0) * 100)}%"
            if item.idle_pct is not None
            else ", idle share unavailable"
        )
        line_items.append(
            f"• `{item.warehouse_name}` — "
            f"{item.projected_monthly_idle_spend_label}/mo "
            f"(period {item.period_idle_spend_label}{idle_fragment})"
        )

    return {
        "text": header,
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": header},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(line_items)},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Open Auto Savings",
                        },
                        "url": "/automated-savings",
                    }
                ],
            },
        ],
    }


async def deliver_digest(
    digest: WasteAlertDigest,
    webhook_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
) -> None:
    """POST the digest to `webhook_url` with linear backoff retries.

    - Empty digests return immediately (no HTTP call).
    - Retries on any `httpx.HTTPError` (transport + non-2xx).
    - Raises `WasteAlertDeliveryError` after the last attempt fails.
    - If the caller passes a `client`, we do not close it. If we
      construct our own, we close it in `finally`.
    """
    if digest.is_empty:
        logger.info("waste_alert_digest_empty: nothing to deliver")
        return

    payload = build_slack_payload(digest)
    owned_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0)

    try:
        for attempt in range(1, max_attempts + 1):
            try:
                response = await http.post(webhook_url, json=payload)
                response.raise_for_status()
                logger.info(
                    "waste_alert_digest_delivered",
                    extra={
                        "item_count": len(digest.items),
                        "attempt": attempt,
                    },
                )
                return
            except httpx.HTTPError as exc:
                if attempt == max_attempts:
                    raise WasteAlertDeliveryError(
                        f"delivery failed after {attempt} attempts: {exc}"
                    ) from exc
                logger.warning(
                    "waste_alert_digest_retry",
                    extra={"attempt": attempt, "error": str(exc)},
                )
                await asyncio.sleep(backoff_seconds * attempt)
    finally:
        if owned_client:
            await http.aclose()

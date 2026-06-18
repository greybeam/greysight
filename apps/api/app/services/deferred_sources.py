"""Registry of deferred (lazy, per-source) dashboard datasets.

A deferred source is fetched on demand via /api/dashboard-runs/{id}/sources/{id}
instead of during the synchronous main run. Each entry knows how to fetch its
rows (resilient) and how to build its view fragment from the run's datasets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from app.services.ai_consumption import fetch_ai_consumption_daily
from app.services.dashboard_view_builder import build_ai_detail_view

ExecuteFn = Callable[[str, dict[str, Any]], list[dict[str, Any]]]


@dataclass(frozen=True)
class DeferredSource:
    id: str
    # (execute, window_days) -> (rows, skipped_branch_ids)
    fetch: Callable[[ExecuteFn, int], tuple[list[dict[str, Any]], list[str]]]


def _fetch_ai(
    execute: ExecuteFn, window_days: int
) -> tuple[list[dict[str, Any]], list[str]]:
    return fetch_ai_consumption_daily(execute, window_days=window_days)


DEFERRED_SOURCES: dict[str, DeferredSource] = {
    "ai_consumption_daily": DeferredSource(id="ai_consumption_daily", fetch=_fetch_ai),
}


def build_ai_detail_from_run(
    *,
    ai_rows: list[dict[str, Any]],
    rate_rows: list[dict[str, Any]],
    currency: str,
    estimated_credit_price_usd: float,
    start_date: date,
    end_date: date,
    partial: bool,
    skipped_branches: list[str],
) -> Any:
    return build_ai_detail_view(
        ai_rows=ai_rows,
        rate_rows=rate_rows,
        currency=currency,
        estimated_credit_price_usd=estimated_credit_price_usd,
        start_date=start_date,
        end_date=end_date,
        partial=partial,
        skipped_branches=skipped_branches,
    )

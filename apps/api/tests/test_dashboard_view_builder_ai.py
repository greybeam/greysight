from datetime import date

from app.services.dashboard_view_builder import _build_ai_spend_summary


def _convert(credits, usage_date, service_type, rating_type=None):
    return credits * 2.0  # flat $2/credit


def test_ai_summary_sums_only_ai_services():
    rows = [
        {"usage_date": date(2026, 6, 1), "service_type": "AI_SERVICES", "credits_used": 3.0},
        {"usage_date": date(2026, 6, 1), "service_type": "CORTEX_AGENTS", "credits_used": 1.0},
        {"usage_date": date(2026, 6, 1), "service_type": "WAREHOUSE_METERING", "credits_used": 99.0},
    ]
    summary = _build_ai_spend_summary(rows=rows, currency="USD", convert=_convert)
    # (3 + 1) credits * $2 = $8, warehouse excluded
    assert summary.total == 8.0
    assert summary.total_label == "$8.00"
    assert summary.is_empty is False


def test_ai_summary_empty_when_no_ai_rows():
    rows = [
        {"usage_date": date(2026, 6, 1), "service_type": "WAREHOUSE_METERING", "credits_used": 99.0},
    ]
    summary = _build_ai_spend_summary(rows=rows, currency="USD", convert=_convert)
    assert summary.total == 0.0
    assert summary.is_empty is True


def test_build_ai_detail_view_breaks_down_by_consumption_type():
    from app.services.dashboard_view_builder import build_ai_detail_view

    ai_rows = [
        {"usage_date": date(2026, 6, 1), "service_type": "AI_SERVICES",
         "consumption_type": "CORTEX_ANALYST", "credits_used": 2.0},
        {"usage_date": date(2026, 6, 1), "service_type": "CORTEX_AGENTS",
         "consumption_type": "CORTEX_AGENTS", "credits_used": 1.0},
    ]
    rate_rows = [
        {"usage_date": date(2026, 6, 1), "service_type": "AI_SERVICES",
         "usage_type": "compute", "rating_type": "AI_COMPUTE", "currency": "USD",
         "effective_rate": 2.0},
        {"usage_date": date(2026, 6, 1), "service_type": "CORTEX_AGENTS",
         "usage_type": "compute", "rating_type": "AI_COMPUTE", "currency": "USD",
         "effective_rate": 2.0},
    ]
    view = build_ai_detail_view(
        ai_rows=ai_rows,
        rate_rows=rate_rows,
        currency="USD",
        estimated_credit_price_usd=2.0,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 1),
        partial=False,
        skipped_branches=[],
    )
    names = set(view.consumption_type_names)
    assert {"CORTEX_ANALYST", "CORTEX_AGENTS"} <= names
    # CORTEX_ANALYST: 2 credits * $2 = $4 ; CORTEX_AGENTS: 1 * $2 = $2
    ranked = {r.name: r.spend for r in view.ranked_consumption_types}
    assert ranked["CORTEX_ANALYST"] == 4.0
    assert ranked["CORTEX_AGENTS"] == 2.0
    assert view.is_empty is False


def test_build_ai_detail_view_empty_and_partial():
    from app.services.dashboard_view_builder import build_ai_detail_view

    view = build_ai_detail_view(
        ai_rows=[],
        rate_rows=[],
        currency="USD",
        estimated_credit_price_usd=2.0,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 2),
        partial=True,
        skipped_branches=["cortex_code_cli"],
    )
    assert view.is_empty is True
    assert view.partial is True
    assert view.skipped_branches == ["cortex_code_cli"]

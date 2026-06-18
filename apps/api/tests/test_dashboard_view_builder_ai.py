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

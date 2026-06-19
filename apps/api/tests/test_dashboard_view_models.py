from app.services.dashboard_view_models import (
    AIConsumptionPoint,
    AIDetailViewModel,
    AISpendSummaryViewModel,
    DashboardViewResponse,
)


def test_ai_summary_model_fields():
    m = AISpendSummaryViewModel(total=12.5, total_label="$12.50", is_empty=False)
    assert m.total == 12.5 and m.total_label == "$12.50" and m.is_empty is False


def test_ai_detail_model_fields():
    m = AIDetailViewModel(
        daily_series=[AIConsumptionPoint(date="2026-06-01", values={"CORTEX_ANALYST": 3.0})],
        consumption_type_names=["CORTEX_ANALYST"],
        ranked_consumption_types=[],
        consumption_bars=[],
        is_empty=False,
        partial=True,
        skipped_branches=["cortex_code_cli"],
    )
    assert m.partial is True
    assert m.skipped_branches == ["cortex_code_cli"]


def test_view_response_requires_ai_summary():
    assert "ai_spend_summary" in DashboardViewResponse.model_fields


def test_section_statuses_defaults_all_ready():
    fields = DashboardViewResponse.model_fields
    assert "section_statuses" in fields
    default = fields["section_statuses"].default_factory()
    assert default == {"overview": "ready", "warehouse": "ready", "storage": "ready"}

from app.models import SAFE_DATASET_ROW_FIELDS, SCHEMA_VERSION
from app.services.deferred_sources import DEFERRED_SOURCES
from app.services.demo_data import (
    DEMO_ACCOUNT_LOCATOR,
    DEMO_ACCOUNT_USAGE_THROUGH,
    DEMO_BILLING_THROUGH,
    DEMO_FETCH_DAYS,
    build_demo_dashboard_dataset,
)

# Fields expected for the ai_consumption_daily deferred-source dataset.
_AI_CONSUMPTION_DAILY_FIELDS = frozenset(
    {"usage_date", "service_type", "consumption_type", "credits_used"}
)
# All datasets produced by the demo builder: core (sendable via create) + deferred.
_DEMO_DATASET_KEYS = frozenset(SAFE_DATASET_ROW_FIELDS) | frozenset(DEFERRED_SOURCES)
# Per-key field schema for ALL demo datasets (core + deferred).
_DEMO_DATASET_ROW_FIELDS: dict[str, frozenset[str]] = {
    **SAFE_DATASET_ROW_FIELDS,
    "ai_consumption_daily": _AI_CONSUMPTION_DAILY_FIELDS,
}


def test_demo_dashboard_dataset_is_deterministic() -> None:
    first = build_demo_dashboard_dataset()
    second = build_demo_dashboard_dataset()

    assert first == second


def test_demo_dashboard_dataset_matches_v0_contract() -> None:
    payload = build_demo_dashboard_dataset()

    assert payload.schema_version == SCHEMA_VERSION
    assert payload.run.id == "demo-run"
    assert payload.run.status == "completed"
    assert payload.run.window_days == DEMO_FETCH_DAYS
    assert payload.metadata.data_mode == "demo"
    assert payload.metadata.account_locator == DEMO_ACCOUNT_LOCATOR
    assert payload.metadata.currency == "USD"
    assert payload.metadata.billing_through_date == DEMO_BILLING_THROUGH
    assert payload.metadata.account_usage_through_date == DEMO_ACCOUNT_USAGE_THROUGH
    assert DEMO_ACCOUNT_USAGE_THROUGH == DEMO_BILLING_THROUGH
    assert payload.metadata.organization_usage.available is True
    assert payload.metadata.account_usage.available is True
    # Core datasets match the create-endpoint allowlist; deferred source datasets
    # (e.g. ai_consumption_daily) are also present for the demo source route.
    assert set(payload.datasets) == _DEMO_DATASET_KEYS

    for dataset_key, rows in payload.datasets.items():
        assert rows, f"{dataset_key} must not be empty"
        expected_fields = _DEMO_DATASET_ROW_FIELDS[dataset_key]
        for row in rows:
            assert set(row) == expected_fields

    assert payload.datasets["current_account"] == [
        {"account_locator": DEMO_ACCOUNT_LOCATOR}
    ]
    assert all(
        "credits_attributed_compute" in row
        for row in payload.datasets["query_compute_by_user_daily"]
    )
    assert all(
        "credits_used_compute" in row
        for row in payload.datasets["warehouse_spend_daily"]
    )
    assert payload.datasets["org_spend_daily"]
    assert payload.datasets["rate_sheet_daily"]
    assert payload.datasets["capacity_balance_daily"]
    assert all(
        row["currency"] == "USD" and row["balance"] > 0
        for row in payload.datasets["capacity_balance_daily"]
    )


def test_demo_usage_date_datasets_cover_100_days() -> None:
    payload = build_demo_dashboard_dataset()
    expected_end = DEMO_BILLING_THROUGH.isoformat()

    for dataset_key, rows in payload.datasets.items():
        if not rows or "usage_date" not in rows[0]:
            continue

        dates = sorted({row["usage_date"] for row in rows})
        assert len(dates) == DEMO_FETCH_DAYS, dataset_key
        assert dates[-1] == expected_end


def test_demo_dashboard_dataset_omits_sensitive_or_raw_fields() -> None:
    payload = build_demo_dashboard_dataset()

    forbidden_raw_keys = {"query_id", "query_text", "sql_text", "private_key"}
    for rows in payload.datasets.values():
        for row in rows:
            assert forbidden_raw_keys.isdisjoint(row)

from app.services.demo_data import build_demo_dashboard_dataset


def test_demo_dashboard_dataset_is_deterministic() -> None:
    first = build_demo_dashboard_dataset()
    second = build_demo_dashboard_dataset()

    assert first == second


def test_demo_dashboard_dataset_has_spec_aligned_chart_ready_shape() -> None:
    payload = build_demo_dashboard_dataset()

    assert payload.run.id == "demo-run"
    assert payload.run.status == "completed"
    assert payload.run.source == "demo"
    assert payload.summary.total_credits > 0
    assert payload.summary.warehouse_count > 0
    assert payload.summary.top_warehouse_name is not None

    datasets = payload.datasets
    required_dataset_keys = {
        "account_spend_daily",
        "warehouse_spend_daily",
        "service_spend_daily",
        "query_compute_by_user_daily",
        "database_storage_daily",
        "top_warehouses_table",
    }
    assert set(datasets) == required_dataset_keys
    for dataset_key in required_dataset_keys:
        assert datasets[dataset_key], dataset_key

    for row in datasets["query_compute_by_user_daily"]:
        assert {
            "usage_date",
            "user_name",
            "warehouse_name",
            "credits_used",
        } <= row.keys()
        assert "query_count" not in row
        assert "cloud_services_credits" not in row

    forbidden_raw_keys = {"query_id", "query_text", "sql_text", "private_key"}
    for rows in datasets.values():
        for row in rows:
            assert forbidden_raw_keys.isdisjoint(row)

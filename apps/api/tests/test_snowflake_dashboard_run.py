from copy import deepcopy
from datetime import date

from fastapi.testclient import TestClient

from app.main import app
from app.routes.dashboard_runs import dashboard_run_repository


def _source_rows(dataset_key: str) -> list[dict[str, object]]:
    rows: dict[str, list[dict[str, object]]] = {
        "service_spend_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "service_type": "WAREHOUSE_METERING",
                "credits_used": 8.0,
            },
            {
                "usage_date": date(2026, 6, 5),
                "service_type": "CLOUD_SERVICES",
                "credits_used": 2.0,
            },
        ],
        "warehouse_spend_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "warehouse_name": "LOAD_WH",
                "credits_used": 8.0,
            },
        ],
        "query_compute_by_user_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "user_name": "ANALYST",
                "warehouse_name": "LOAD_WH",
                "credits_used": 8.0,
            },
        ],
        "database_storage_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "database_name": "RAW",
                "average_database_bytes": 1_000_000_000_000,
                "average_failsafe_bytes": 0,
            },
        ],
    }
    return deepcopy(rows[dataset_key])


def test_snowflake_run_executes_registered_sources_and_persists_datasets(
    monkeypatch,
) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "snowflake")
    executed_sources: list[str] = []

    def execute(sql: str, bind_params: dict[str, object]):
        assert "%(window_days)s" in sql
        assert bind_params == {"window_days": 30}
        for dataset_key in [
            "warehouse_spend_daily",
            "service_spend_daily",
            "query_compute_by_user_daily",
            "database_storage_daily",
        ]:
            if dataset_key in sql or _known_source_sql_matches(dataset_key, sql):
                executed_sources.append(dataset_key)
                return _source_rows(dataset_key)
        raise AssertionError("unexpected unregistered SQL")

    monkeypatch.setattr("app.routes.dashboard_runs.execute_source_query", execute)

    client = TestClient(app)
    run_response = client.post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
        },
    )

    assert run_response.status_code == 201
    run = run_response.json()
    assert run["status"] == "completed"
    assert run["source"] == "snowflake"
    assert sorted(executed_sources) == [
        "database_storage_daily",
        "query_compute_by_user_daily",
        "service_spend_daily",
        "warehouse_spend_daily",
    ]

    executed_sources.clear()
    datasets_response = client.get(f"/api/dashboard-runs/{run['id']}/datasets")

    assert datasets_response.status_code == 200
    body = datasets_response.json()
    assert body["datasets"]["account_spend_daily"] == [
        {"usage_date": "2026-06-05", "credits_used": 10.0}
    ]
    assert body["summary"]["total_credits"] == 10.0
    assert executed_sources == []


def test_snowflake_run_failure_returns_safe_error(monkeypatch) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "snowflake")

    def fail_query(sql: str, bind_params: dict[str, object]):
        raise RuntimeError("raw private backend detail")

    monkeypatch.setattr("app.routes.dashboard_runs.execute_source_query", fail_query)

    response = TestClient(app).post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
        },
    )

    assert response.status_code == 502
    assert "raw private backend detail" not in response.text
    assert response.json()["detail"] == "Could not query Snowflake Account Usage."


def _known_source_sql_matches(dataset_key: str, sql: str) -> bool:
    markers = {
        "warehouse_spend_daily": "warehouse_metering_history",
        "service_spend_daily": "metering_daily_history",
        "query_compute_by_user_daily": "query_attribution_history",
        "database_storage_daily": "database_storage_usage_history",
    }
    return markers[dataset_key] in sql.lower()

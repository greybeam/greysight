from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from app.main import app
from app.models import SCHEMA_VERSION
from app.routes.dashboard_runs import dashboard_run_repository
from app.services.dashboard_datasets import FETCH_WINDOW_DAYS
from app.services.snowflake_client import SnowflakeQueryError


def test_snowflake_dashboard_run_uses_v0_builder_and_persists_metadata(
    monkeypatch,
) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "snowflake")
    executed_groups: list[str] = []

    def execute(sql: str, bind_params: dict[str, Any]) -> list[dict[str, Any]]:
        lowered = sql.lower()
        if "current_account()" in lowered:
            assert bind_params == {}
            executed_groups.append("metadata")
            return [{"account_locator": "TU24199"}]
        if "remaining_balance_daily" in lowered:
            assert bind_params == {
                "window_days": FETCH_WINDOW_DAYS,
                "account_locator": "TU24199",
            }
            executed_groups.append("capacity")
            return [
                {
                    "usage_date": date(2026, 6, 5),
                    "currency": "USD",
                    "balance": 15_000.0,
                }
            ]
        if "organization_usage" in lowered:
            assert bind_params == {
                "window_days": FETCH_WINDOW_DAYS,
                "account_locator": "TU24199",
            }
            executed_groups.append("org")
            if "usage_in_currency_daily" in lowered:
                return [
                    {
                        "usage_date": date(2026, 6, 5),
                        "service_type": "WAREHOUSE_METERING",
                        "rating_type": "COMPUTE",
                        "billing_type": "CONSUMPTION",
                        "is_adjustment": False,
                        "currency": "USD",
                        "spend": 24.0,
                    }
                ]
            return [
                {
                    "usage_date": date(2026, 6, 5),
                    "service_type": "WAREHOUSE_METERING",
                    "rating_type": "COMPUTE",
                    "currency": "USD",
                    "effective_rate": 3.0,
                }
            ]

        assert bind_params == {"window_days": FETCH_WINDOW_DAYS}
        executed_groups.append("account")
        return _source_rows(_source_key_for_sql(lowered))

    monkeypatch.setattr("app.services.dashboard_datasets.execute_source_query", execute)

    run_response = TestClient(app).post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
        },
    )

    assert run_response.status_code == 201
    run = run_response.json()
    assert run["window_days"] == FETCH_WINDOW_DAYS

    datasets_response = TestClient(app).get(f"/api/dashboard-runs/{run['id']}/datasets")
    assert datasets_response.status_code == 200
    body = datasets_response.json()
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["summary"]["total_credits"] == 10.0
    assert body["metadata"]["data_mode"] == "billed"
    assert body["metadata"]["account_locator"] == "TU24199"
    assert body["metadata"]["currency"] == "USD"
    assert body["metadata"]["organization_usage"]["available"] is True
    assert body["metadata"]["account_usage"]["available"] is True
    assert {"org_spend_daily", "rate_sheet_daily", "current_account"} <= set(
        body["datasets"]
    )
    assert body["datasets"]["account_spend_daily"] == [
        {"usage_date": "2026-03-01", "credits_used": 5.0},
        {"usage_date": "2026-06-05", "credits_used": 10.0},
    ]
    assert executed_groups.count("metadata") == 1
    assert executed_groups.count("org") == 2
    assert executed_groups.count("capacity") == 1
    assert executed_groups.count("account") == 4


def test_snowflake_dashboard_run_returns_neutral_error_when_sources_fail(
    monkeypatch,
) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "snowflake")

    def fail_query(sql: str, bind_params: dict[str, Any]) -> list[dict[str, Any]]:
        if "current_account()" in sql.lower():
            return [{"account_locator": "TU24199"}]
        raise SnowflakeQueryError("raw private backend detail")

    monkeypatch.setattr(
        "app.services.dashboard_datasets.execute_source_query", fail_query
    )

    response = TestClient(app).post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
        },
    )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Could not query Snowflake billing or Account Usage data."
    )


def test_snowflake_dashboard_run_does_not_expose_unexpected_backend_detail(
    monkeypatch,
) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "snowflake")

    def execute(sql: str, bind_params: dict[str, Any]) -> list[dict[str, Any]]:
        lowered = sql.lower()
        if "current_account()" in lowered:
            assert bind_params == {}
            return [{"account_locator": "TU24199"}]
        if "remaining_balance_daily" in lowered:
            assert bind_params == {
                "window_days": FETCH_WINDOW_DAYS,
                "account_locator": "TU24199",
            }
            return [
                {
                    "usage_date": date(2026, 6, 5),
                    "currency": "USD",
                    "balance": 15_000.0,
                }
            ]
        if "organization_usage" in lowered:
            assert bind_params == {
                "window_days": FETCH_WINDOW_DAYS,
                "account_locator": "TU24199",
            }
            if "usage_in_currency_daily" in lowered:
                return [
                    {
                        "usage_date": date(2026, 6, 5),
                        "service_type": "WAREHOUSE_METERING",
                        "rating_type": "COMPUTE",
                        "billing_type": "CONSUMPTION",
                        "is_adjustment": False,
                        "currency": "USD",
                        "spend": 24.0,
                    }
                ]
            return [
                {
                    "usage_date": date(2026, 6, 5),
                    "service_type": "WAREHOUSE_METERING",
                    "rating_type": "COMPUTE",
                    "currency": "USD",
                    "effective_rate": 3.0,
                }
            ]

        assert bind_params == {"window_days": FETCH_WINDOW_DAYS}
        return _source_rows(_source_key_for_sql(lowered))

    def raise_private_detail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("raw private backend detail")

    monkeypatch.setattr("app.services.dashboard_datasets.execute_source_query", execute)
    monkeypatch.setattr(
        "app.services.dashboard_datasets.build_dashboard_summary",
        raise_private_detail,
    )

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
        },
    )

    assert response.status_code == 500
    assert "raw private backend detail" not in response.text


def test_snowflake_dashboard_run_falls_back_to_estimated_mode(
    monkeypatch,
) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "snowflake")

    def execute(sql: str, bind_params: dict[str, Any]) -> list[dict[str, Any]]:
        lowered = sql.lower()
        if "current_account()" in lowered:
            return [{"account_locator": "TU24199"}]
        if "organization_usage" in lowered:
            raise SnowflakeQueryError("org unavailable")
        assert bind_params == {"window_days": FETCH_WINDOW_DAYS}
        return _source_rows(_source_key_for_sql(lowered))

    monkeypatch.setattr("app.services.dashboard_datasets.execute_source_query", execute)

    run_response = TestClient(app).post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 7,
        },
    )

    assert run_response.status_code == 201
    datasets_response = TestClient(app).get(
        f"/api/dashboard-runs/{run_response.json()['id']}/datasets"
    )
    body = datasets_response.json()
    assert body["metadata"]["data_mode"] == "estimated"
    assert body["metadata"]["organization_usage"]["available"] is False
    assert body["metadata"]["account_usage"]["available"] is True
    assert body["datasets"]["org_spend_daily"] == []
    assert body["datasets"]["rate_sheet_daily"] == []


def test_snowflake_dashboard_run_view_route_returns_prepared_view(monkeypatch) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "snowflake")

    def execute(sql: str, bind_params: dict[str, Any]) -> list[dict[str, Any]]:
        lowered = sql.lower()
        if "current_account()" in lowered:
            return [{"account_locator": "TU24199"}]
        if "remaining_balance_daily" in lowered:
            return [
                {
                    "usage_date": date(2026, 6, 5),
                    "currency": "USD",
                    "balance": 15_000.0,
                }
            ]
        if "organization_usage" in lowered:
            if "usage_in_currency_daily" in lowered:
                return [
                    {
                        "usage_date": date(2026, 6, 5),
                        "service_type": "WAREHOUSE_METERING",
                        "rating_type": "COMPUTE",
                        "billing_type": "CONSUMPTION",
                        "is_adjustment": False,
                        "currency": "USD",
                        "spend": 24.0,
                    }
                ]
            return [
                {
                    "usage_date": date(2026, 6, 5),
                    "service_type": "WAREHOUSE_METERING",
                    "rating_type": "COMPUTE",
                    "currency": "USD",
                    "effective_rate": 3.0,
                }
            ]
        return _source_rows(_source_key_for_sql(lowered))

    monkeypatch.setattr("app.services.dashboard_datasets.execute_source_query", execute)
    client = TestClient(app)
    run_response = client.post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
        },
    )
    run_id = run_response.json()["id"]

    view_response = client.get(f"/api/dashboard-runs/{run_id}/view")

    assert view_response.status_code == 200
    body = view_response.json()
    assert body["run"]["id"] == run_id
    assert body["header"]["data_mode_label"] == "Billed"
    assert body["header"]["account_locator"] == "TU24199"
    assert body["total_spend"]["basis"] == "billed"


def test_snowflake_dashboard_view_rejects_too_old_custom_range(monkeypatch) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "snowflake")
    monkeypatch.setattr(
        "app.services.dashboard_datasets.execute_source_query",
        lambda sql, bind_params: [{"account_locator": "TU24199"}]
        if "current_account()" in sql.lower()
        else _source_rows(_source_key_for_sql(sql.lower()))
        if "organization_usage" not in sql.lower()
        else [],
    )
    client = TestClient(app)
    run_response = client.post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
        },
    )
    run_id = run_response.json()["id"]

    response = client.get(
        f"/api/dashboard-runs/{run_id}/view",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "range_out_of_bounds"
    assert (
        response.json()["detail"]["source_start_date"]
        <= response.json()["detail"]["source_end_date"]
    )


def test_snowflake_dashboard_view_rejects_invalid_window_days(monkeypatch) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "snowflake")
    monkeypatch.setattr(
        "app.services.dashboard_datasets.execute_source_query",
        lambda sql, bind_params: [{"account_locator": "TU24199"}]
        if "current_account()" in sql.lower()
        else _source_rows(_source_key_for_sql(sql.lower()))
        if "organization_usage" not in sql.lower()
        else [],
    )
    client = TestClient(app)
    run_id = client.post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
        },
    ).json()["id"]

    response = client.get(
        f"/api/dashboard-runs/{run_id}/view",
        params={"window_days": 15},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_range"


def _source_rows(dataset_key: str) -> list[dict[str, object]]:
    rows: dict[str, list[dict[str, object]]] = {
        "service_spend_daily": [
            {
                "usage_date": date(2026, 3, 1),
                "service_type": "WAREHOUSE_METERING",
                "credits_used": 5.0,
            },
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
                "credits_used_compute": 7.5,
            },
        ],
        "query_compute_by_user_daily": [
            {
                "usage_date": date(2026, 6, 5),
                "user_name": "ANALYST",
                "warehouse_name": "LOAD_WH",
                "credits_attributed_compute": 8.0,
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
    return rows[dataset_key]


def _source_key_for_sql(sql: str) -> str:
    markers = {
        "warehouse_spend_daily": "warehouse_metering_history",
        "service_spend_daily": "metering_daily_history",
        "query_compute_by_user_daily": "query_attribution_history",
        "database_storage_daily": "database_storage_usage_history",
    }
    for dataset_key, marker in markers.items():
        if marker in sql:
            return dataset_key
    raise AssertionError(f"unexpected unregistered SQL: {sql}")

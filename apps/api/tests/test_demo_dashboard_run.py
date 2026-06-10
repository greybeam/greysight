from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app
from app.routes.dashboard_runs import dashboard_run_repository


def test_demo_run_returns_completed_run_and_datasets() -> None:
    client = TestClient(app)

    run_response = client.get("/api/dashboard-runs/demo")
    datasets_response = client.get("/api/dashboard-runs/demo/datasets")

    assert run_response.status_code == 200
    assert run_response.json()["status"] == "completed"
    assert datasets_response.status_code == 200
    assert "service_spend_daily" in datasets_response.json()["datasets"]


def test_persisted_run_round_trips_aggregate_datasets() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)

    create_response = client.post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
            "summary": {"total_credits": 12.5},
            "datasets": {
                "service_spend_daily": [
                    {
                        "usage_date": "2026-06-07",
                        "service_type": "WAREHOUSE_METERING",
                        "credits_used": 12.5,
                    }
                ]
            },
        },
    )

    assert create_response.status_code == 201
    created_run = create_response.json()
    UUID(created_run["id"])
    assert created_run["status"] == "completed"
    assert created_run["source"] == "snowflake"

    run_response = client.get(f"/api/dashboard-runs/{created_run['id']}")
    datasets_response = client.get(f"/api/dashboard-runs/{created_run['id']}/datasets")

    assert run_response.status_code == 200
    assert run_response.json()["id"] == created_run["id"]
    assert datasets_response.status_code == 200
    assert datasets_response.json()["summary"] == {"total_credits": 12.5}
    assert (
        datasets_response.json()["datasets"]["service_spend_daily"][0]["service_type"]
        == "WAREHOUSE_METERING"
    )


def test_deleted_run_no_longer_returns_datasets() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    create_response = client.post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
            "summary": {},
            "datasets": {"service_spend_daily": []},
        },
    )
    run_id = create_response.json()["id"]

    delete_response = client.delete(f"/api/dashboard-runs/{run_id}")
    datasets_response = client.get(f"/api/dashboard-runs/{run_id}/datasets")

    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"
    assert datasets_response.status_code == 404


def test_create_run_rejects_raw_snowflake_fields() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)

    response = client.post(
        "/api/dashboard-runs",
        json={
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "source": "snowflake",
            "window_days": 30,
            "summary": {},
            "datasets": {
                "query_compute_by_user_daily": [
                    {
                        "usage_date": "2026-06-07",
                        "user_name": "ANALYST_A",
                        "credits_used": 10.0,
                        "query_text": "select * from sensitive_table",
                    }
                ]
            },
        },
    )

    assert response.status_code == 422


def test_demo_route_is_not_captured_by_uuid_run_route() -> None:
    client = TestClient(app)

    response = client.get("/api/dashboard-runs/demo")

    assert response.status_code == 200
    assert response.json()["id"] == "demo-run"


def test_local_dashboard_origin_can_read_demo_datasets() -> None:
    client = TestClient(app)

    response = client.get(
        "/api/dashboard-runs/demo/datasets",
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"

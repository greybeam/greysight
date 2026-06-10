from copy import deepcopy
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app
from app.routes.dashboard_runs import dashboard_run_repository
from app.services.demo_data import build_demo_dashboard_dataset


def _complete_create_payload() -> dict[str, object]:
    demo_payload = build_demo_dashboard_dataset()
    return {
        "organization_id": "00000000-0000-0000-0000-000000000001",
        "source": "snowflake",
        "window_days": 30,
        "summary": demo_payload.summary.model_dump(mode="json"),
        "datasets": deepcopy(demo_payload.datasets),
    }


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
        json=_complete_create_payload(),
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
    assert datasets_response.json()["summary"]["total_credits"] == 132.0
    assert (
        datasets_response.json()["datasets"]["service_spend_daily"][0]["service_type"]
        == "WAREHOUSE_METERING"
    )


def test_deleted_run_keeps_readable_tombstone_without_datasets() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    create_response = client.post(
        "/api/dashboard-runs",
        json=_complete_create_payload(),
    )
    run_id = create_response.json()["id"]

    delete_response = client.delete(f"/api/dashboard-runs/{run_id}")
    run_response = client.get(f"/api/dashboard-runs/{run_id}")
    datasets_response = client.get(f"/api/dashboard-runs/{run_id}/datasets")

    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "deleted"
    assert datasets_response.status_code == 404


def test_create_run_rejects_raw_snowflake_fields() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    payload = _complete_create_payload()
    datasets = payload["datasets"]
    assert isinstance(datasets, dict)
    query_rows = datasets["query_compute_by_user_daily"]
    assert isinstance(query_rows, list)
    query_rows[0]["query_text"] = "select * from sensitive_table"

    response = client.post("/api/dashboard-runs", json=payload)

    assert response.status_code == 422


def test_create_run_rejects_unknown_dataset_keys() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    payload = _complete_create_payload()
    datasets = payload["datasets"]
    assert isinstance(datasets, dict)
    datasets["raw_query_history"] = [{"query_text": "select current_user()"}]

    response = client.post("/api/dashboard-runs", json=payload)

    assert response.status_code == 422


def test_create_run_requires_all_dashboard_datasets() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    payload = _complete_create_payload()
    datasets = payload["datasets"]
    assert isinstance(datasets, dict)
    datasets.pop("service_spend_daily")

    response = client.post("/api/dashboard-runs", json=payload)

    assert response.status_code == 422


def test_expired_persisted_datasets_are_unavailable_and_mark_run_expired() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    payload = _complete_create_payload()
    payload["retention_days"] = 1
    create_response = client.post("/api/dashboard-runs", json=payload)
    run_id = create_response.json()["id"]
    dashboard_run_repository.expire_run_datasets(UUID(run_id))

    datasets_response = client.get(f"/api/dashboard-runs/{run_id}/datasets")
    run_response = client.get(f"/api/dashboard-runs/{run_id}")

    assert datasets_response.status_code == 404
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "expired"


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

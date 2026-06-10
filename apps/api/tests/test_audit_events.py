from copy import deepcopy

from fastapi.testclient import TestClient

from app.main import app
from app.routes.dashboard_runs import dashboard_run_repository
from app.services.audit_events import audit_event_recorder
from app.services.snowflake_client import SnowflakeValidationError
from app.services.demo_data import build_demo_dashboard_dataset


def _complete_create_payload(
    organization_id: str | None = "00000000-0000-0000-0000-000000000001",
) -> dict[str, object]:
    demo_payload = build_demo_dashboard_dataset()
    payload: dict[str, object] = {
        "source": "snowflake",
        "window_days": 30,
        "summary": demo_payload.summary.model_dump(mode="json"),
        "datasets": deepcopy(demo_payload.datasets),
    }
    if organization_id is not None:
        payload["organization_id"] = organization_id
    return payload


def setup_function() -> None:
    audit_event_recorder.clear()
    dashboard_run_repository.clear()


def test_validation_attempt_records_local_audit_without_sensitive_details(
    monkeypatch,
) -> None:
    def fail_validation() -> None:
        raise SnowflakeValidationError("raw private backend detail")

    monkeypatch.setattr(
        "app.routes.snowflake.validate_snowflake_connection",
        fail_validation,
    )

    response = TestClient(app).post("/api/snowflake/validate")

    assert response.status_code == 403
    assert audit_event_recorder.list_events() == [
        {
            "event_name": "snowflake.validation_attempted",
            "organization_id": None,
            "payload": {"outcome": "failed"},
        }
    ]
    assert "raw private backend detail" not in str(audit_event_recorder.list_events())


def test_org_dashboard_run_lifecycle_records_sanitized_audit_events() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/dashboard-runs",
        json=_complete_create_payload(),
    )
    run_id = create_response.json()["id"]

    datasets_response = client.get(f"/api/dashboard-runs/{run_id}/datasets")
    delete_response = client.delete(f"/api/dashboard-runs/{run_id}")

    assert create_response.status_code == 201
    assert datasets_response.status_code == 200
    assert delete_response.status_code == 200
    events = audit_event_recorder.list_events()
    assert [event["event_name"] for event in events] == [
        "dashboard_run.created",
        "dashboard_run.dataset_retrieved",
        "dashboard_run.deleted",
    ]
    assert {event["organization_id"] for event in events} == {
        "00000000-0000-0000-0000-000000000001"
    }
    assert events[0]["payload"] == {
        "run_id": run_id,
        "source": "snowflake",
        "status": "completed",
        "window_days": 30,
        "dataset_keys": [
            "account_spend_daily",
            "database_storage_daily",
            "query_compute_by_user_daily",
            "service_spend_daily",
            "top_warehouses_table",
            "warehouse_spend_daily",
        ],
    }
    assert events[1]["payload"] == {
        "run_id": run_id,
        "dataset_keys": [
            "account_spend_daily",
            "database_storage_daily",
            "query_compute_by_user_daily",
            "service_spend_daily",
            "top_warehouses_table",
            "warehouse_spend_daily",
        ],
    }
    assert events[2]["payload"] == {"run_id": run_id, "status": "deleted"}
    assert "WAREHOUSE_METERING" not in str(events)
    assert "select *" not in str(events).lower()


def test_no_org_dashboard_run_skips_org_scoped_audit_events() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/dashboard-runs",
        json=_complete_create_payload(organization_id=None),
    )
    run_id = create_response.json()["id"]

    datasets_response = client.get(f"/api/dashboard-runs/{run_id}/datasets")
    delete_response = client.delete(f"/api/dashboard-runs/{run_id}")

    assert create_response.status_code == 201
    assert datasets_response.status_code == 200
    assert delete_response.status_code == 200
    assert audit_event_recorder.list_events() == []

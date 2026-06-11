from copy import deepcopy
from datetime import date
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app
from app.models import DashboardRunCreateRequest
from app.routes.dashboard_runs import dashboard_run_repository
from app.services.demo_data import DEMO_FETCH_DAYS, build_demo_dashboard_dataset

ORG_ONE = "00000000-0000-0000-0000-000000000001"
ORG_TWO = "00000000-0000-0000-0000-000000000002"


def _complete_create_payload() -> dict[str, object]:
    demo_payload = build_demo_dashboard_dataset()
    return {
        "organization_id": ORG_ONE,
        "source": "snowflake",
        "window_days": 30,
        "summary": demo_payload.summary.model_dump(mode="json"),
        "datasets": deepcopy(demo_payload.datasets),
    }


def _verified_token_for_org(monkeypatch, organization_id: str) -> dict[str, str]:
    def verifier(token: str) -> dict[str, object]:
        assert token == "valid-token"
        return {
            "sub": "user-1",
            "app_metadata": {"organization_ids": [organization_id]},
        }

    monkeypatch.setattr("app.auth.supabase_session_verifier", verifier)
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    return {"Authorization": "Bearer valid-token"}


def test_demo_run_returns_completed_run_and_datasets() -> None:
    client = TestClient(app)

    run_response = client.get("/api/dashboard-runs/demo")
    datasets_response = client.get("/api/dashboard-runs/demo/datasets")

    assert run_response.status_code == 200
    assert run_response.json()["status"] == "completed"
    assert datasets_response.status_code == 200
    assert "service_spend_daily" in datasets_response.json()["datasets"]


def test_demo_view_route_returns_default_prepared_view() -> None:
    client = TestClient(app)

    response = client.get("/api/dashboard-runs/demo/view")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert body["run"]["id"] == "demo-run"
    assert body["range"] == {
        "mode": "relative",
        "window_days": 30,
        "start_date": "2026-05-10",
        "end_date": "2026-06-08",
    }
    assert body["header"]["data_mode_label"] == "Demo"
    assert body["header"]["freshness_label"] == "Demo data through Jun 8, 2026"
    assert body["total_spend"]["projection_basis_label"] == "latest 30 days"


def test_demo_view_clamps_custom_end_date_to_through_date() -> None:
    response = TestClient(app).get(
        "/api/dashboard-runs/demo/view",
        params={"start_date": "2026-06-01", "end_date": "2026-06-11"},
    )

    assert response.status_code == 200
    assert response.json()["range"] == {
        "mode": "custom",
        "window_days": None,
        "start_date": "2026-06-01",
        "end_date": "2026-06-08",
    }


def test_create_dashboard_run_requires_auth_when_enabled(monkeypatch) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("AUTH_REQUIRED", "true")

    response = TestClient(app).post(
        "/api/dashboard-runs",
        json=_complete_create_payload(),
    )

    assert response.status_code in {401, 403}
    assert response.json()["detail"] == "Authentication required"


def test_create_dashboard_run_rejects_non_member_organization(monkeypatch) -> None:
    dashboard_run_repository.clear()
    headers = _verified_token_for_org(monkeypatch, ORG_ONE)
    payload = _complete_create_payload()
    payload["organization_id"] = ORG_TWO

    response = TestClient(app).post(
        "/api/dashboard-runs",
        json=payload,
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Organization access denied"


def test_auth_required_demo_source_creates_complete_org_scoped_datasets(
    monkeypatch,
) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "demo")
    headers = _verified_token_for_org(monkeypatch, ORG_ONE)

    create_response = TestClient(app).post(
        "/api/dashboard-runs",
        json={
            "organization_id": ORG_ONE,
            "source": "snowflake",
            "window_days": 30,
        },
        headers=headers,
    )

    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    datasets_response = TestClient(app).get(
        f"/api/dashboard-runs/{run_id}/datasets",
        headers=headers,
    )

    assert datasets_response.status_code == 200
    body = datasets_response.json()
    assert body["metadata"]["data_mode"] == "demo"
    datasets = body["datasets"]
    assert len(datasets["service_spend_daily"]) > 0
    assert len(datasets["warehouse_spend_daily"]) > 0


def test_auth_required_demo_source_pins_run_to_demo_window(monkeypatch) -> None:
    dashboard_run_repository.clear()
    monkeypatch.setenv("DATA_SOURCE", "demo")
    headers = _verified_token_for_org(monkeypatch, ORG_ONE)

    create_response = TestClient(app).post(
        "/api/dashboard-runs",
        json={
            "organization_id": ORG_ONE,
            "source": "snowflake",
            "window_days": 7,
        },
        headers=headers,
    )

    assert create_response.status_code == 201
    assert create_response.json()["window_days"] == DEMO_FETCH_DAYS


def test_persisted_run_routes_reject_non_member_organization(monkeypatch) -> None:
    dashboard_run_repository.clear()
    payload = _complete_create_payload()
    payload["organization_id"] = ORG_TWO
    created_run = dashboard_run_repository.create_completed_run(
        DashboardRunCreateRequest.model_validate(payload)
    )
    headers = _verified_token_for_org(monkeypatch, ORG_ONE)

    run_response = TestClient(app).get(
        f"/api/dashboard-runs/{created_run.id}",
        headers=headers,
    )
    datasets_response = TestClient(app).get(
        f"/api/dashboard-runs/{created_run.id}/datasets",
        headers=headers,
    )
    view_response = TestClient(app).get(
        f"/api/dashboard-runs/{created_run.id}/view",
        headers=headers,
    )
    delete_response = TestClient(app).delete(
        f"/api/dashboard-runs/{created_run.id}",
        headers=headers,
    )

    assert run_response.status_code == 403
    assert datasets_response.status_code == 403
    assert view_response.status_code == 403
    assert delete_response.status_code == 403


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
    body = datasets_response.json()
    demo_payload = build_demo_dashboard_dataset()
    assert body["summary"]["total_credits"] == demo_payload.summary.total_credits
    assert (
        datasets_response.json()["datasets"]["service_spend_daily"][0]["service_type"]
        == "WAREHOUSE_METERING"
    )


def test_view_route_does_not_expose_dataset_invariant_errors_as_range_errors() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app, raise_server_exceptions=False)
    payload = _complete_create_payload()
    datasets = payload["datasets"]
    assert isinstance(datasets, dict)
    org_spend_rows = datasets["org_spend_daily"]
    assert isinstance(org_spend_rows, list)
    org_spend_rows[0]["usage_date"] = "2026-06-08"
    org_spend_rows[0]["spend"] = "not-a-number"

    create_response = client.post("/api/dashboard-runs", json=payload)
    run_id = create_response.json()["id"]

    response = client.get(f"/api/dashboard-runs/{run_id}/view")

    assert create_response.status_code == 201
    assert response.status_code == 500
    assert "invalid_range" not in response.text
    assert "org_spend_daily.spend" not in response.text


def test_view_route_does_not_expose_corrupted_source_bounds_as_range_errors() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app, raise_server_exceptions=False)
    create_response = client.post(
        "/api/dashboard-runs",
        json=_complete_create_payload(),
    )
    run_id = UUID(create_response.json()["id"])
    bounds = dashboard_run_repository.get_source_bounds(run_id)
    assert bounds is not None
    bounds.source_start_date = date(2026, 6, 9)
    bounds.source_end_date = date(2026, 6, 8)

    response = client.get(f"/api/dashboard-runs/{run_id}/view")

    assert create_response.status_code == 201
    assert response.status_code == 500
    assert "invalid_range" not in response.text
    assert "source bounds start_date" not in response.text


def test_deleted_run_keeps_readable_tombstone_without_datasets() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    create_response = client.post(
        "/api/dashboard-runs",
        json=_complete_create_payload(),
    )
    run_id = create_response.json()["id"]
    parsed_run_id = UUID(run_id)

    assert dashboard_run_repository.get_source_bounds(parsed_run_id) is not None

    delete_response = client.delete(f"/api/dashboard-runs/{run_id}")
    run_response = client.get(f"/api/dashboard-runs/{run_id}")
    datasets_response = client.get(f"/api/dashboard-runs/{run_id}/datasets")

    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "deleted"
    assert datasets_response.status_code == 404
    assert dashboard_run_repository.get_source_bounds(parsed_run_id) is None


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


def test_create_run_accepts_empty_aggregate_dataset_rows() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    payload = _complete_create_payload()
    datasets = payload["datasets"]
    assert isinstance(datasets, dict)
    datasets["query_compute_by_user_daily"] = []

    response = client.post("/api/dashboard-runs", json=payload)

    assert response.status_code == 201


def test_create_run_rejects_malformed_usage_date() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app, raise_server_exceptions=False)
    payload = _complete_create_payload()
    datasets = payload["datasets"]
    assert isinstance(datasets, dict)
    service_rows = datasets["service_spend_daily"]
    assert isinstance(service_rows, list)
    service_rows[0]["usage_date"] = "not-a-date"

    response = client.post("/api/dashboard-runs", json=payload)

    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert "service_spend_daily[0]" in detail
    assert "usage_date" in detail


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


def test_completed_run_persists_source_bounds() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    create_response = client.post(
        "/api/dashboard-runs",
        json=_complete_create_payload(),
    )
    run_id = create_response.json()["id"]

    bounds = dashboard_run_repository.get_source_bounds(UUID(run_id))

    assert bounds is not None
    assert bounds.source_start_date.isoformat() == "2026-03-01"
    assert bounds.source_end_date.isoformat() == "2026-06-08"


def test_expired_run_removes_source_bounds() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    create_response = client.post(
        "/api/dashboard-runs",
        json=_complete_create_payload(),
    )
    run_id = UUID(create_response.json()["id"])
    dashboard_run_repository.expire_run_datasets(run_id)

    response = client.get(f"/api/dashboard-runs/{run_id}/datasets")

    assert response.status_code == 404
    assert dashboard_run_repository.get_source_bounds(run_id) is None


def test_view_route_404_for_expired_run() -> None:
    dashboard_run_repository.clear()
    client = TestClient(app)
    run_id = UUID(
        client.post(
            "/api/dashboard-runs",
            json=_complete_create_payload(),
        ).json()["id"]
    )
    dashboard_run_repository.expire_run_datasets(run_id)

    # Hitting /view must lazily expire the run (like /datasets) and drop its
    # source bounds, rather than serving a prepared view from expired data.
    response = TestClient(app).get(f"/api/dashboard-runs/{run_id}/view")

    assert response.status_code == 404
    assert dashboard_run_repository.get_source_bounds(run_id) is None


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

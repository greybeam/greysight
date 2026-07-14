import json

import httpx
import pytest

from app.services.automated_savings_store import (
    AutomatedSavingsStoreError,
    SupabaseAutomatedSavingsStore,
)


def _store(handler) -> SupabaseAutomatedSavingsStore:
    return SupabaseAutomatedSavingsStore(
        supabase_url="https://project.supabase.co",
        service_role_key="service-role-key",
        transport=httpx.MockTransport(handler),
    )


def test_unenroll_calls_guarded_disable_rpc() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=True)

    store = _store(handler)
    store.unenroll("org-1", "WH1")

    assert len(requests) == 1
    request = requests[0]
    assert "rpc/automated_savings_disable_enrollment" in str(request.url)
    assert json.loads(request.content) == {
        "p_organization_id": "org-1",
        "p_warehouse_name": "WH1",
    }


def test_unenroll_fails_when_enrollment_does_not_exist() -> None:
    store = _store(lambda request: httpx.Response(200, json=False))

    with pytest.raises(AutomatedSavingsStoreError) as exc_info:
        store.unenroll("org-1", "MISSING")

    assert exc_info.value.kind == "not_found"


def test_enroll_calls_upsert_rpc_with_identity_only() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    store = _store(handler)
    store.upsert_enrollment(
        "org-1",
        "WH1",
        enabled=True,
        warehouse_created_on="2024-01-01T00:00:00Z",
    )

    assert len(requests) == 1
    request = requests[0]
    assert "rpc/automated_savings_upsert_enrollment" in str(request.url)
    assert json.loads(request.content) == {
        "p_organization_id": "org-1",
        "p_warehouse_name": "WH1",
        "p_enabled": True,
        "p_warehouse_created_on": "2024-01-01T00:00:00Z",
    }


def test_list_warehouses_reads_identity_only_enrollment_shape() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "organization_id": "org-1",
                    "warehouse_name": "WH1",
                    "enabled": True,
                    "warehouse_created_on": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-02T00:00:00+00:00",
                }
            ],
        )

    [row] = _store(handler).list_warehouses("org-1")

    assert vars(row) == {
        "organization_id": "org-1",
        "warehouse_name": "WH1",
        "enabled": True,
        "warehouse_created_on": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00+00:00",
    }
    assert requests[0].url.params["select"] == (
        "organization_id,warehouse_name,enabled,warehouse_created_on,updated_at"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("organization_id", ""),
        ("organization_id", "   "),
        ("organization_id", 123),
        ("warehouse_name", ""),
        ("warehouse_name", 123),
        ("enabled", "true"),
        ("enabled", 1),
        ("warehouse_created_on", None),
        ("warehouse_created_on", "2024-01-01"),
        ("warehouse_created_on", "2024-01-01T00:00:00"),
        ("warehouse_created_on", "invalid"),
        ("updated_at", None),
        ("updated_at", "2024-01-02"),
        ("updated_at", "2024-01-02T00:00:00"),
        ("updated_at", "invalid"),
    ],
)
def test_list_warehouses_rejects_malformed_postgrest_rows(field, value) -> None:
    row = {
        "organization_id": "org-1",
        "warehouse_name": "WH1",
        "enabled": True,
        "warehouse_created_on": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00+00:00",
    }
    if value is None:
        row.pop(field)
    else:
        row[field] = value

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row])

    with pytest.raises(AutomatedSavingsStoreError):
        _store(handler).list_warehouses("org-1")


def test_upsert_enrollment_raises_on_non_success_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    store = _store(handler)
    with pytest.raises(AutomatedSavingsStoreError):
        store.upsert_enrollment(
            "org-1",
            "WH1",
            enabled=True,
            warehouse_created_on="2024-01-01T00:00:00Z",
        )

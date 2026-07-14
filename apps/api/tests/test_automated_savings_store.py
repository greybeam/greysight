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


def _drifted_store(value: int) -> tuple[SupabaseAutomatedSavingsStore, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[{
                "organization_id": "org-1", "warehouse_name": "WH1", "enabled": True,
                "managed_auto_suspend": 300, "stored_default_auto_suspend": 300,
                "warehouse_created_on": None, "cooldown_ts": None,
                "drift_state": "drifted", "drifted_value": value,
            }])
        return httpx.Response(204)

    return _store(handler), requests


def test_unenroll_only_clears_enabled_never_writes_intent() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    store = _store(handler)
    store.unenroll("org-1", "WH1")

    assert len(requests) == 1
    request = requests[0]
    assert "automated_savings_warehouses" in str(request.url)
    assert "automated_savings_restore_intents" not in str(request.url)
    payload = json.loads(request.content)
    assert payload == {
        "organization_id": "org-1",
        "warehouse_name": "WH1",
        "enabled": False,
    }


def test_reconcile_accept_adopts_drifted_value_and_clears_drift() -> None:
    store, requests = _drifted_store(900)
    store.reconcile("org-1", "WH1", accept=True)

    writes = [r for r in requests if r.method == "POST"]
    assert len(writes) == 1
    payload = json.loads(writes[0].content)
    assert payload["managed_auto_suspend"] == 900
    assert payload["drift_state"] == "ok"
    assert payload["drifted_value"] is None
    assert "automated_savings_restore_intents" not in str(writes[0].url)


def test_reconcile_accept_ignores_drifted_value_below_floor() -> None:
    store, requests = _drifted_store(30)
    store.reconcile("org-1", "WH1", accept=True)

    writes = [r for r in requests if r.method == "POST"]
    payload = json.loads(writes[0].content)
    assert "managed_auto_suspend" not in payload
    assert payload["drift_state"] == "ok"


def test_reconcile_reject_enqueues_restore_intent_and_clears_drift() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[{
                "organization_id": "org-1", "warehouse_name": "WH1",
                "enabled": True, "managed_auto_suspend": 300,
                "stored_default_auto_suspend": 300, "warehouse_created_on": None,
                "cooldown_ts": None, "drift_state": "drifted",
                "drifted_value": 900,
            }])
        return httpx.Response(200, json=True)

    store = _store(handler)
    store.reconcile("org-1", "WH1", accept=False)

    writes = [r for r in requests if r.method == "POST"]
    assert len(writes) == 1
    assert "rpc/automated_savings_enqueue_reapply" in str(writes[0].url)
    assert json.loads(writes[0].content) == {
        "p_organization_id": "org-1",
        "p_warehouse_name": "WH1",
        "p_restore_to": 300,
        "p_expected_from": 900,
    }


def test_reconcile_reject_reports_atomic_enqueue_conflict() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[{
                "organization_id": "org-1", "warehouse_name": "WH1",
                "enabled": True, "managed_auto_suspend": 300,
                "stored_default_auto_suspend": 300, "warehouse_created_on": None,
                "cooldown_ts": None, "drift_state": "drifted",
                "drifted_value": 900,
            }])
        requests.append(request)
        return httpx.Response(200, json=False)

    store = _store(handler)

    with pytest.raises(AutomatedSavingsStoreError):
        store.reconcile("org-1", "WH1", accept=False)

    assert len(requests) == 1


def test_enroll_calls_upsert_rpc_with_captured_defaults() -> None:
    # Every enroll call — first enroll or re-enroll — sends the freshly
    # captured stored_default/managed_default straight through to the atomic
    # upsert RPC. The RPC (not this client) decides whether to keep them or
    # preserve an existing capture, and it always has valid values to insert
    # even if the row was concurrently deleted (see finding #5 fix). The
    # preserve-vs-fresh-capture SQL logic is exercised in
    # test_automated_savings_migration.py.
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    store = _store(handler)
    store.upsert_enrollment(
        "org-1",
        "WH1",
        enabled=True,
        stored_default=300,
        managed_default=300,
        warehouse_created_on="2024-01-01T00:00:00Z",
    )

    assert len(requests) == 1
    request = requests[0]
    assert "rpc/automated_savings_upsert_enrollment" in str(request.url)
    payload = json.loads(request.content)
    assert payload == {
        "p_organization_id": "org-1",
        "p_warehouse_name": "WH1",
        "p_enabled": True,
        "p_stored_default": 300,
        "p_managed_default": 300,
        "p_warehouse_created_on": "2024-01-01T00:00:00Z",
    }


def test_upsert_enrollment_raises_on_non_success_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    store = _store(handler)
    with pytest.raises(AutomatedSavingsStoreError):
        store.upsert_enrollment(
            "org-1",
            "WH1",
            enabled=True,
            stored_default=300,
            managed_default=300,
            warehouse_created_on=None,
        )

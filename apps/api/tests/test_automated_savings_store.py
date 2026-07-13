import json

import httpx

from app.services.automated_savings_store import SupabaseAutomatedSavingsStore


def _store(handler) -> SupabaseAutomatedSavingsStore:
    return SupabaseAutomatedSavingsStore(
        supabase_url="https://project.supabase.co",
        service_role_key="service-role-key",
        transport=httpx.MockTransport(handler),
    )


def test_set_managed_default_writes_managed_auto_suspend() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    store = _store(handler)
    store.set_managed_default("org-1", "WH1", 900)

    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["managed_auto_suspend"] == 900
    assert "automated_savings_warehouses" in str(requests[0].url)


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
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "organization_id": "org-1",
                        "warehouse_name": "WH1",
                        "enabled": True,
                        "managed_auto_suspend": 300,
                        "stored_default_auto_suspend": 300,
                        "warehouse_created_on": None,
                        "cooldown_ts": None,
                        "drift_state": "drifted",
                        "drifted_value": 900,
                    }
                ],
            )
        return httpx.Response(204)

    store = _store(handler)
    store.reconcile("org-1", "WH1", accept=True)

    writes = [r for r in requests if r.method == "POST"]
    assert len(writes) == 1
    payload = json.loads(writes[0].content)
    assert payload["managed_auto_suspend"] == 900
    assert payload["drift_state"] == "ok"
    assert payload["drifted_value"] is None
    assert "automated_savings_restore_intents" not in str(writes[0].url)


def test_reconcile_accept_ignores_drifted_value_below_floor() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "organization_id": "org-1",
                        "warehouse_name": "WH1",
                        "enabled": True,
                        "managed_auto_suspend": 300,
                        "stored_default_auto_suspend": 300,
                        "warehouse_created_on": None,
                        "cooldown_ts": None,
                        "drift_state": "drifted",
                        "drifted_value": 30,
                    }
                ],
            )
        return httpx.Response(204)

    store = _store(handler)
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
            return httpx.Response(
                200,
                json=[
                    {
                        "organization_id": "org-1",
                        "warehouse_name": "WH1",
                        "enabled": True,
                        "managed_auto_suspend": 300,
                        "stored_default_auto_suspend": 300,
                        "warehouse_created_on": None,
                        "cooldown_ts": None,
                        "drift_state": "drifted",
                        "drifted_value": 900,
                    }
                ],
            )
        return httpx.Response(204)

    store = _store(handler)
    store.reconcile("org-1", "WH1", accept=False)

    writes = [r for r in requests if r.method == "POST"]
    intent_writes = [r for r in writes if "automated_savings_restore_intents" in str(r.url)]
    warehouse_writes = [r for r in writes if "automated_savings_warehouses" in str(r.url)]

    assert len(intent_writes) == 1
    intent_payload = json.loads(intent_writes[0].content)
    assert intent_payload["restore_to"] == 300  # the managed_auto_suspend, not drifted_value

    assert len(warehouse_writes) == 1
    warehouse_payload = json.loads(warehouse_writes[0].content)
    assert warehouse_payload["drift_state"] == "ok"
    assert warehouse_payload["drifted_value"] is None
    assert "managed_auto_suspend" not in warehouse_payload


def test_enroll_seeds_managed_default_from_stored_default() -> None:
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
        warehouse_created_on=None,
    )

    payload = json.loads(requests[0].content)
    assert payload["stored_default_auto_suspend"] == 300
    assert payload["managed_auto_suspend"] == 300

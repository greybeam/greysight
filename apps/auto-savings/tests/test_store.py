from datetime import datetime, timezone
import json

import httpx
import pytest

from auto_savings.config import WorkerConfig
from auto_savings.store import (
    EnrollmentRow,
    InMemoryStore,
    SavingsEvent,
    SettingsRow,
    StoreError,
    SupabaseStore,
)

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _config() -> WorkerConfig:
    return WorkerConfig(
        supabase_url="https://x.supabase.co", supabase_service_role_key="svc"
    )


def _enrollment_row(organization_id: str, warehouse_name: str, enabled: bool) -> EnrollmentRow:
    return EnrollmentRow(
        organization_id=organization_id,
        warehouse_name=warehouse_name,
        enabled=enabled,
        managed_auto_suspend=60,
        stored_default_auto_suspend=300,
        warehouse_created_on=NOW,
        cooldown_ts=None,
        drift_state="ok",
        drifted_value=None,
    )


def test_write_intent_resets_sentinel_confirmed_to_false():
    # An upsert over a confirmed intent must NOT inherit stale confirmation: a fresh
    # write starts a new, unconfirmed ownership claim (so a re-armed sentinel cannot
    # be read as already-confirmed by the stale-SHOW that armed it).
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300, cycle_id="c1")
    store.confirm_sentinel("org-1", "WH1", "c1")
    store.write_intent("org-1", "WH1", restore_to=300)
    assert store.list_intents("org-1")[0].sentinel_confirmed is False


@pytest.mark.parametrize(
    ("kind", "cycle_id"),
    [("sentinel", "replacement"), ("reapply", "c1")],
)
def test_in_memory_confirm_sentinel_rejects_replaced_or_non_sentinel_intent(
    kind: str, cycle_id: str
):
    store = InMemoryStore()
    store.write_intent(
        "org-1", "WH1", restore_to=300, cycle_id=cycle_id, kind=kind
    )

    with pytest.raises(StoreError):
        store.confirm_sentinel("org-1", "WH1", "c1")


def test_supabase_store_writes_complete_unconfirmed_intent_via_postgrest():
    seen = {}
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("authorization")
        bodies.append(request.read().decode())
        return httpx.Response(201, json=[])

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))
    store.write_intent(
        "org-1", "WH1", restore_to=300, expected_from=300,
        baseline_resumed_on=NOW, cycle_id="c1"
    )
    store.write_intent("org-1", "WH1", restore_to=300)

    assert "automated_savings_restore_intents" in seen["url"]
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer svc"
    assert f'"baseline_resumed_on":"{NOW.isoformat()}"' in bodies[0]
    assert '"expected_from":300' in bodies[0]
    assert '"sentinel_confirmed":false' in bodies[0]
    assert '"cycle_id":"c1"' in bodies[0]
    assert '"baseline_resumed_on":null' in bodies[1]


def test_supabase_store_confirm_sentinel_patches_restore_intent():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json=[
                {
                    "organization_id": "org-1",
                    "warehouse_name": "WH1",
                    "cycle_id": "c1",
                    "kind": "sentinel",
                    "sentinel_confirmed": True,
                }
            ],
        )

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))
    store.confirm_sentinel("org-1", "WH1", "c1")

    assert "automated_savings_restore_intents" in seen["url"]
    assert seen["method"] == "PATCH"
    assert "organization_id=eq.org-1" in seen["url"]
    assert "warehouse_name=eq.WH1" in seen["url"]
    assert "cycle_id=eq.c1" in seen["url"]
    assert "kind=eq.sentinel" in seen["url"]
    assert '"sentinel_confirmed":true' in seen["body"]


def test_supabase_store_delete_intent_is_cycle_and_kind_cas():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["prefer"] = request.headers.get("prefer")
        return httpx.Response(200, json=[{
            "organization_id": "org-1",
            "warehouse_name": "WH1",
            "cycle_id": "c1",
            "kind": "sentinel",
        }])

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    assert store.delete_intent("org-1", "WH1", "c1", "sentinel") is True
    assert "cycle_id=eq.c1" in seen["url"]
    assert "kind=eq.sentinel" in seen["url"]
    assert seen["prefer"] == "return=representation"


@pytest.mark.parametrize(("response", "expected"), [(True, True), (False, False)])
def test_supabase_store_cleanup_uses_atomic_rpc(response, expected):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json=response)

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))
    result = store.cleanup_intent_and_enrollment(
        "org-1", "WH1", "c1", "sentinel"
    )

    assert result is expected
    assert "rpc/automated_savings_cleanup_intent" in seen["url"]
    body = json.loads(seen["body"])
    assert body == {
        "p_organization_id": "org-1",
        "p_warehouse_name": "WH1",
        "p_cycle_id": "c1",
        "p_kind": "sentinel",
    }


@pytest.mark.parametrize(("response", "expected"), [(True, True), (False, False)])
def test_supabase_store_no_intent_cleanup_uses_locking_rpc(response, expected):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json=response)

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))
    result = store.cleanup_enrollment_if_no_intent("org-1", "WH1")

    assert result is expected
    assert "rpc/automated_savings_cleanup_enrollment_if_no_intent" in seen["url"]
    assert json.loads(seen["body"]) == {
        "p_organization_id": "org-1",
        "p_warehouse_name": "WH1",
    }


@pytest.mark.parametrize(
    "response_json",
    [
        [],
        {},
        [
            {
                "organization_id": "org-1",
                "warehouse_name": "WH1",
                "cycle_id": "replacement",
                "kind": "sentinel",
                "sentinel_confirmed": True,
            }
        ],
        [
            {
                "organization_id": "org-1",
                "warehouse_name": "WH1",
                "cycle_id": "c1",
                "kind": "reapply",
                "sentinel_confirmed": True,
            }
        ],
        [
            {
                "organization_id": "org-1",
                "warehouse_name": "WH1",
                "cycle_id": "c1",
                "kind": "sentinel",
                "sentinel_confirmed": True,
            },
            {
                "organization_id": "org-1",
                "warehouse_name": "WH1",
                "cycle_id": "c1",
                "kind": "sentinel",
                "sentinel_confirmed": True,
            },
        ],
    ],
)
def test_supabase_store_confirm_sentinel_requires_exactly_one_matching_row(
    response_json: object,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_json)

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    with pytest.raises(StoreError):
        store.confirm_sentinel("org-1", "WH1", "c1")


def test_supabase_store_list_intents_hydrates_ownership_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "organization_id": "org-1",
                    "warehouse_name": "WH1",
                    "restore_to": 300,
                    "expected_from": 120,
                    "set_at": NOW.isoformat(),
                    "baseline_resumed_on": NOW.isoformat(),
                    "cycle_id": "c1",
                    "kind": "sentinel",
                    "sentinel_confirmed": True,
                }
            ],
        )

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))
    [intent] = store.list_intents("org-1")

    assert intent.sentinel_confirmed is True
    assert intent.expected_from == 120
    assert intent.baseline_resumed_on == NOW
    assert intent.cycle_id == "c1"


def test_supabase_store_list_intents_defaults_sentinel_confirmed_false_when_absent():
    # A pre-migration row (column absent) hydrates as unconfirmed, not an error.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "organization_id": "org-1",
                    "warehouse_name": "WH1",
                    "restore_to": 300,
                    "set_at": NOW.isoformat(),
                    "baseline_resumed_on": None,
                    "cycle_id": "c1",
                    "kind": "sentinel",
                }
            ],
        )

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))
    [intent] = store.list_intents("org-1")

    assert intent.sentinel_confirmed is False


def test_supabase_store_record_event_posts_to_events_table():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = request.read().decode()
        return httpx.Response(201, json=[])

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))
    store.record_event(
        SavingsEvent(
            organization_id="org-1", warehouse_name="WH1", action="restore",
            reason="suspended", to_value=300, observed_at=NOW, from_value=1,
            observed_state="SUSPENDED", cycle_id="c1",
        )
    )

    assert "automated_savings_events" in seen["url"]
    assert seen["method"] == "POST"
    assert '"reason":"suspended"' in seen["body"]
    assert '"cycle_id":"c1"' in seen["body"]


def test_supabase_store_list_enrollments_tolerates_null_partial_row():
    # A partial/unenrolled row (managed/stored/created_on all null) must parse to
    # Nones, not raise and fail the entire tenant cycle (finding #6).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "organization_id": "org-1",
                    "warehouse_name": "WH1",
                    "enabled": False,
                    "managed_auto_suspend": None,
                    "stored_default_auto_suspend": None,
                    "warehouse_created_on": None,
                    "cooldown_ts": None,
                    "drift_state": None,
                    "drifted_value": None,
                }
            ],
        )

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))
    [row] = store.list_enrollments("org-1")

    assert row.warehouse_name == "WH1"
    assert row.managed_auto_suspend is None
    assert row.stored_default_auto_suspend is None
    assert row.warehouse_created_on is None


def test_in_memory_worker_tenants_requires_enabled_warehouse():
    store = InMemoryStore()
    # global_enabled but no enabled warehouse -> not a worker tenant.
    store.seed_settings(
        SettingsRow(
            organization_id="org-1",
            agreed_at=NOW,
            global_enabled=True,
            grant_present=True,
            grant_checked_at=NOW,
        )
    )
    store.seed_enrollment(_enrollment_row("org-1", "WH1", enabled=False))

    # global_enabled with an enabled warehouse -> worker tenant.
    store.seed_settings(
        SettingsRow(
            organization_id="org-2",
            agreed_at=NOW,
            global_enabled=True,
            grant_present=True,
            grant_checked_at=NOW,
        )
    )
    store.seed_enrollment(_enrollment_row("org-2", "WH1", enabled=True))

    # outstanding intent but globally disabled -> still a worker tenant (drain).
    store.seed_settings(
        SettingsRow(
            organization_id="org-3",
            agreed_at=NOW,
            global_enabled=False,
            grant_present=True,
            grant_checked_at=NOW,
        )
    )
    store.write_intent("org-3", "WH1", restore_to=300)

    assert store.worker_tenants() == ["org-2", "org-3"]

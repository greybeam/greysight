from datetime import datetime, timedelta, timezone
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
    _parse_enrollment,
)

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
CREATED_ON = NOW - timedelta(days=1)


def _config() -> WorkerConfig:
    return WorkerConfig(
        supabase_url="https://x.supabase.co", supabase_service_role_key="svc"
    )


def _enrollment(
    organization_id: str = "org-1", *, enabled: bool = True
) -> EnrollmentRow:
    return EnrollmentRow(
        organization_id=organization_id,
        warehouse_name="WH1",
        enabled=enabled,
        warehouse_created_on=CREATED_ON,
        updated_at=NOW,
    )


def _settings(
    organization_id: str = "org-1", *, global_enabled: bool = True
) -> SettingsRow:
    return SettingsRow(
        organization_id=organization_id,
        agreed_at=NOW,
        global_enabled=global_enabled,
        grant_present=True,
        grant_checked_at=NOW,
    )


def _direct_event() -> SavingsEvent:
    return SavingsEvent(
        organization_id="org-1",
        warehouse_name="WH1",
        action="suspend",
        reason="idle",
        observed_state="STARTED",
        observed_running=0,
        observed_queued=0,
        observed_quiescing=0,
        observed_resumed_on=CREATED_ON,
        observed_started_clusters=1,
        observed_min_cluster_count=None,
        observed_max_cluster_count=3,
        observed_at=NOW,
    )


def _seed_direct_store(
    *, global_enabled: bool = True, enrollment_enabled: bool = True
) -> InMemoryStore:
    store = InMemoryStore()
    store.seed_settings(_settings(global_enabled=global_enabled))
    store.seed_enrollment(_enrollment(enabled=enrollment_enabled))
    return store


def test_authorize_suspend_accepts_only_current_enabled_state():
    store = _seed_direct_store()

    assert (
        store.authorize_suspend(
            "org-1",
            "WH1",
            warehouse_created_on=CREATED_ON,
            enrollment_updated_at=NOW,
        )
        is True
    )


@pytest.mark.parametrize(
    ("change", "created_on", "updated_at"),
    [
        ("kill-switch", CREATED_ON, NOW),
        ("disabled", CREATED_ON, NOW),
        ("identity", CREATED_ON + timedelta(seconds=1), NOW),
        ("version", CREATED_ON, NOW + timedelta(seconds=1)),
    ],
    ids=["kill-switch", "disabled", "identity", "version"],
)
def test_authorize_suspend_rejects_stale_or_disabled_state(
    change: str, created_on: datetime, updated_at: datetime
):
    store = _seed_direct_store(
        global_enabled=change != "kill-switch",
        enrollment_enabled=change != "disabled",
    )
    assert (
        store.authorize_suspend(
            "org-1",
            "WH1",
            warehouse_created_on=created_on,
            enrollment_updated_at=updated_at,
        )
        is False
    )


def test_delete_stale_enrollment_requires_exact_identity_and_version():
    store = _seed_direct_store()

    assert (
        store.delete_stale_enrollment(
            "org-1",
            "WH1",
            warehouse_created_on=CREATED_ON,
            enrollment_updated_at=NOW + timedelta(seconds=1),
        )
        is False
    )
    assert store.list_enrollments("org-1") == [_enrollment()]
    assert (
        store.delete_stale_enrollment(
            "org-1",
            "WH1",
            warehouse_created_on=CREATED_ON,
            enrollment_updated_at=NOW,
        )
        is True
    )
    assert store.list_enrollments("org-1") == []


@pytest.mark.parametrize(
    ("method", "path", "response", "expected"),
    [
        (
            "authorize_suspend",
            "automated_savings_authorize_suspend",
            True,
            True,
        ),
        (
            "authorize_suspend",
            "automated_savings_authorize_suspend",
            False,
            False,
        ),
        (
            "delete_stale_enrollment",
            "automated_savings_delete_stale_enrollment",
            True,
            True,
        ),
        (
            "delete_stale_enrollment",
            "automated_savings_delete_stale_enrollment",
            False,
            False,
        ),
    ],
)
def test_supabase_store_calls_direct_state_rpc(
    method: str, path: str, response: bool, expected: bool
):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = json.loads(request.read())
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=response)

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))
    result = getattr(store, method)(
        "org-1",
        "WH1",
        warehouse_created_on=CREATED_ON,
        enrollment_updated_at=NOW,
    )

    assert result is expected
    assert path in str(seen["url"])
    assert seen["method"] == "POST"
    assert seen["authorization"] == "Bearer svc"
    assert seen["body"] == {
        "p_organization_id": "org-1",
        "p_warehouse_name": "WH1",
        "p_warehouse_created_on": CREATED_ON.isoformat(),
        "p_enrollment_updated_at": NOW.isoformat(),
    }


@pytest.mark.parametrize("response", [None, 1, "true", [], {}, [True]])
@pytest.mark.parametrize("method", ["authorize_suspend", "delete_stale_enrollment"])
def test_supabase_direct_state_rpc_rejects_non_boolean_response(
    method: str, response: object
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    with pytest.raises(StoreError):
        getattr(store, method)(
            "org-1",
            "WH1",
            warehouse_created_on=CREATED_ON,
            enrollment_updated_at=NOW,
        )


@pytest.mark.parametrize("method", ["authorize_suspend", "delete_stale_enrollment"])
@pytest.mark.parametrize("failure", ["transport", "status", "json"])
def test_supabase_direct_state_rpc_fails_safely(method: str, failure: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "transport":
            raise httpx.ConnectError("offline", request=request)
        if failure == "status":
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, content=b"not-json")

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    with pytest.raises(StoreError):
        getattr(store, method)(
            "org-1",
            "WH1",
            warehouse_created_on=CREATED_ON,
            enrollment_updated_at=NOW,
        )


def test_supabase_rpc_status_error_has_safe_operation_context():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"message": "tenant-secret svc must not leak"},
            request=request,
        )

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    with pytest.raises(StoreError) as raised:
        store.authorize_suspend(
            "org-1",
            "WH1",
            warehouse_created_on=CREATED_ON,
            enrollment_updated_at=NOW,
        )

    message = str(raised.value)
    assert message == "authorize suspend failed with HTTP 503"
    assert "tenant-secret" not in message
    assert "svc" not in message
    assert "https://" not in message


def test_supabase_rpc_malformed_result_describes_expected_and_actual_kind():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"secret": "tenant-secret"})

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    with pytest.raises(StoreError) as raised:
        store.delete_stale_enrollment(
            "org-1",
            "WH1",
            warehouse_created_on=CREATED_ON,
            enrollment_updated_at=NOW,
        )

    message = str(raised.value)
    assert message == "delete stale enrollment expected boolean result, got object"
    assert "tenant-secret" not in message


@pytest.mark.parametrize(
    ("response", "expected_message"),
    [
        ({}, "worker tenants expected list result, got object"),
        (["org-1"], "worker tenants expected object at item 0, got string"),
        (
            [{"wrong": "org-1"}],
            "worker tenants item 0 has invalid organization_id",
        ),
    ],
)
def test_supabase_worker_tenants_malformed_result_has_safe_shape_context(
    response: object, expected_message: str
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    with pytest.raises(StoreError) as raised:
        store.worker_tenants()

    assert str(raised.value) == expected_message


def test_direct_event_payload_omits_legacy_fields():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.read())
        return httpx.Response(201, json=[])

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))
    store.record_event(_direct_event())

    assert "automated_savings_events" in str(seen["url"])
    assert seen["body"] == {
        "organization_id": "org-1",
        "warehouse_name": "WH1",
        "action": "suspend",
        "reason": "idle",
        "observed_state": "STARTED",
        "observed_running": 0,
        "observed_queued": 0,
        "observed_quiescing": 0,
        "observed_resumed_on": CREATED_ON.isoformat(),
        "observed_started_clusters": 1,
        "observed_min_cluster_count": None,
        "observed_max_cluster_count": 3,
        "observed_at": NOW.isoformat(),
    }


def test_list_enrollments_requires_complete_identity_and_version():
    row = {
        "organization_id": "org-1",
        "warehouse_name": "WH1",
        "enabled": True,
        "warehouse_created_on": CREATED_ON.isoformat(),
        "updated_at": NOW.isoformat(),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row])

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    assert store.list_enrollments("org-1") == [_enrollment()]


@pytest.mark.parametrize("missing", ["warehouse_created_on", "updated_at"])
def test_list_enrollments_rejects_missing_identity_or_version(missing: str):
    row = {
        "organization_id": "org-1",
        "warehouse_name": "WH1",
        "enabled": True,
        "warehouse_created_on": CREATED_ON.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    row[missing] = None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row])

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    with pytest.raises(StoreError):
        store.list_enrollments("org-1")


@pytest.mark.parametrize("field", ["warehouse_created_on", "updated_at"])
@pytest.mark.parametrize(
    "value",
    [
        "2026-07-12T12:00:00",
        "2026-07-12",
        "not-a-timestamp",
        None,
    ],
    ids=["offset-less", "date-only", "invalid", "null"],
)
def test_list_enrollments_rejects_non_timezone_aware_identity_or_version(
    field: str, value: object
):
    row: dict[str, object] = {
        "organization_id": "org-1",
        "warehouse_name": "WH1",
        "enabled": True,
        "warehouse_created_on": CREATED_ON.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    row[field] = value

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row])

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    with pytest.raises(StoreError):
        store.list_enrollments("org-1")


@pytest.mark.parametrize("field", ["warehouse_created_on", "updated_at"])
def test_parse_enrollment_rejects_naive_datetime_identity_or_version(field: str):
    row: dict[str, object] = {
        "organization_id": "org-1",
        "warehouse_name": "WH1",
        "enabled": True,
        "warehouse_created_on": CREATED_ON.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    row[field] = datetime(2026, 7, 12, 12, 0, 0)

    with pytest.raises(StoreError):
        _parse_enrollment(row)


@pytest.mark.parametrize("field", ["warehouse_created_on", "updated_at"])
@pytest.mark.parametrize(
    "value",
    ["2026-07-12T12:00:00Z", "2026-07-12T05:00:00-07:00"],
    ids=["utc", "offset"],
)
def test_list_enrollments_accepts_timezone_aware_identity_and_version(
    field: str, value: str
):
    row = {
        "organization_id": "org-1",
        "warehouse_name": "WH1",
        "enabled": True,
        "warehouse_created_on": CREATED_ON.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    row[field] = value

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row])

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    [enrollment] = store.list_enrollments("org-1")
    assert getattr(enrollment, field).utcoffset() is not None


def test_worker_tenants_requires_global_switch_and_enabled_enrollment():
    store = InMemoryStore()
    store.seed_settings(_settings("org-1", global_enabled=True))
    store.seed_enrollment(_enrollment("org-1", enabled=False))
    store.seed_settings(_settings("org-2", global_enabled=True))
    store.seed_enrollment(_enrollment("org-2", enabled=True))
    store.seed_settings(_settings("org-3", global_enabled=False))
    store.seed_enrollment(_enrollment("org-3", enabled=True))

    assert store.worker_tenants() == ["org-2"]


@pytest.mark.parametrize("response", [{}, ["org-1"], [{"wrong": "org-1"}]])
def test_supabase_worker_tenants_rejects_malformed_rows(response: object):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    store = SupabaseStore(_config(), transport=httpx.MockTransport(handler))

    with pytest.raises(StoreError):
        store.worker_tenants()

import contextlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import anyio
import httpx
import pytest

from app.services.automated_savings_store import (
    AutomatedSavingsStoreError,
    DailySuspensionsRow,
    EventRow,
    SupabaseAutomatedSavingsStore,
)
from app.services.dashboard_run_cache import SupabaseRunCacheStore
from app.services.http_pool import clear_clients, get_sync_client, install_clients


@contextlib.contextmanager
def _installed_sync_pool(handler):
    clear_clients()
    sync_client = httpx.Client(transport=httpx.MockTransport(handler))
    auth = httpx.AsyncClient()
    async_client = httpx.AsyncClient()
    install_clients(auth=auth, async_client=async_client, sync_client=sync_client)
    try:
        yield sync_client
    finally:
        clear_clients()
        sync_client.close()
        anyio.run(auth.aclose)
        anyio.run(async_client.aclose)


def test_savings_store_reuses_pooled_sync_client_without_closing() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    with _installed_sync_pool(handler) as sync_client:
        store = SupabaseAutomatedSavingsStore(
            supabase_url="https://project.supabase.co",
            service_role_key="service-role-key",
        )
        store.get_settings("org-1")
        assert get_sync_client() is sync_client
        assert not sync_client.is_closed
        assert len(requests) == 1


def test_two_sequential_savings_calls_reuse_open_shared_client() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    with _installed_sync_pool(handler) as sync_client:
        store = SupabaseAutomatedSavingsStore(
            supabase_url="https://project.supabase.co",
            service_role_key="service-role-key",
        )
        store.get_settings("org-1")
        assert not sync_client.is_closed
        store.list_warehouses("org-1")
        assert not sync_client.is_closed
        assert get_sync_client() is sync_client
        assert len(requests) == 2

        run_store = SupabaseRunCacheStore(
            supabase_url="https://project.supabase.co",
            service_role_key="service-role-key",
        )
        run_store.delete("org-1")
        assert not sync_client.is_closed
        assert get_sync_client() is sync_client
        assert len(requests) == 3


def test_pooled_requests_isolate_credentials_when_concurrent() -> None:
    # Both requests must be in-flight through the shared client at the same
    # time; a Barrier inside the transport handler blocks each request until
    # the other has also arrived, so neither can complete first.
    lock = threading.Lock()
    requests_by_key: dict[str, httpx.Request] = {}
    barrier = threading.Barrier(2)

    def handler(request: httpx.Request) -> httpx.Response:
        # Rendezvous: forces genuine concurrency before either response starts.
        barrier.wait(timeout=5)
        with lock:
            requests_by_key[request.headers["apikey"]] = request
        return httpx.Response(200, json=[])

    with _installed_sync_pool(handler) as sync_client:
        store_a = SupabaseAutomatedSavingsStore(
            supabase_url="https://project.supabase.co",
            service_role_key="key-a",
        )
        store_b = SupabaseAutomatedSavingsStore(
            supabase_url="https://project.supabase.co",
            service_role_key="key-b",
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(store_a.get_settings, "org-1"),
                pool.submit(store_b.get_settings, "org-1"),
            ]
            for future in futures:
                future.result(timeout=5)

        # Each recorded request carries only its own credentials.
        request_a = requests_by_key["key-a"]
        request_b = requests_by_key["key-b"]
        assert request_a.headers["authorization"] == "Bearer key-a"
        assert request_b.headers["authorization"] == "Bearer key-b"
        # The shared pool carries no per-request credentials.
        assert "apikey" not in sync_client.headers
        assert "authorization" not in sync_client.headers


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


def _rpc_store(handler):
    return SupabaseAutomatedSavingsStore(
        supabase_url="https://example.supabase.co",
        service_role_key="key",
        transport=httpx.MockTransport(handler),
    )


def test_daily_suspensions_calls_rpc_and_parses_rows():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.read())
        return httpx.Response(
            200,
            json=[
                {
                    "day": "2026-07-14",
                    "warehouse_name": "COMPUTE_WH",
                    "suspension_count": 3,
                },
                {
                    "day": "2026-07-15",
                    "warehouse_name": "ANALYTICS_WH",
                    "suspension_count": 1,
                },
            ],
        )

    rows = _rpc_store(handler).daily_suspensions("org-1", 7, "2026-07-15")

    assert captured["url"].endswith("/rest/v1/rpc/automated_savings_daily_suspensions")
    assert captured["json"] == {
        "p_organization_id": "org-1",
        "p_day_count": 7,
        "p_end_day": "2026-07-15",
    }
    assert rows == [
        DailySuspensionsRow(
            day="2026-07-14", warehouse_name="COMPUTE_WH", suspension_count=3
        ),
        DailySuspensionsRow(
            day="2026-07-15", warehouse_name="ANALYTICS_WH", suspension_count=1
        ),
    ]


def test_list_events_passes_cursor_and_parses_rows():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.read())
        return httpx.Response(
            200,
            json=[
                {
                    "id": 42,
                    "created_at": "2026-07-15T10:00:00+00:00",
                    "warehouse_name": "COMPUTE_WH",
                    "action": "suspend",
                    "reason": "idle",
                    "observed_started_clusters": 1,
                    "observed_resumed_on": "2026-07-15T08:00:00+00:00",
                    "observed_at": "2026-07-15T09:59:00+00:00",
                }
            ],
        )

    rows = _rpc_store(handler).list_events(
        "org-1",
        limit=26,
        cursor_created_at="2026-07-15T11:00:00+00:00",
        cursor_id=99,
    )

    assert captured["url"].endswith("/rest/v1/rpc/automated_savings_events_page")
    assert captured["json"] == {
        "p_organization_id": "org-1",
        "p_page_limit": 26,
        "p_cursor_created_at": "2026-07-15T11:00:00+00:00",
        "p_cursor_id": 99,
    }
    assert rows == [
        EventRow(
            id=42,
            created_at="2026-07-15T10:00:00+00:00",
            warehouse_name="COMPUTE_WH",
            action="suspend",
            reason="idle",
            observed_started_clusters=1,
            observed_resumed_on="2026-07-15T08:00:00+00:00",
            observed_at="2026-07-15T09:59:00+00:00",
        )
    ]


def test_list_events_nullable_fields_allowed_and_malformed_row_raises():
    def ok_handler(request):
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "created_at": "2026-07-15T10:00:00+00:00",
                    "warehouse_name": "WH",
                    "action": "suspend",
                    "reason": "idle",
                    "observed_started_clusters": None,
                    "observed_resumed_on": None,
                    "observed_at": "2026-07-15T09:59:00+00:00",
                }
            ],
        )

    row = _rpc_store(ok_handler).list_events("org-1", limit=1)[0]
    assert row.observed_resumed_on is None
    assert row.observed_started_clusters is None

    def bad_handler(request):
        return httpx.Response(200, json=[{"id": "not-an-int"}])

    with pytest.raises(AutomatedSavingsStoreError) as exc_info:
        _rpc_store(bad_handler).list_events("org-1", limit=1)
    assert exc_info.value.kind == "malformed_row"


def test_daily_suspensions_rejects_invalid_calendar_day():
    def handler(request):
        return httpx.Response(
            200,
            json=[
                {
                    "day": "2026-13-40",
                    "warehouse_name": "COMPUTE_WH",
                    "suspension_count": 3,
                }
            ],
        )

    with pytest.raises(AutomatedSavingsStoreError) as exc_info:
        _rpc_store(handler).daily_suspensions("org-1", 7, "2026-07-15")
    assert exc_info.value.kind == "malformed_row"


def test_daily_suspensions_rejects_non_canonical_day():
    # Python 3.11+'s date.fromisoformat also accepts basic-format ("20260715")
    # and ISO-week ("2026-W29-4") strings; only canonical YYYY-MM-DD may pass.
    def handler(request):
        return httpx.Response(
            200,
            json=[
                {
                    "day": "20260715",
                    "warehouse_name": "COMPUTE_WH",
                    "suspension_count": 3,
                }
            ],
        )

    with pytest.raises(AutomatedSavingsStoreError) as exc_info:
        _rpc_store(handler).daily_suspensions("org-1", 7, "2026-07-15")
    assert exc_info.value.kind == "malformed_row"


@pytest.mark.parametrize(
    "missing_key",
    ["observed_started_clusters", "observed_resumed_on"],
)
def test_list_events_missing_nullable_event_key_raises_malformed_row(
    missing_key,
):
    event_row = {
        "id": 1,
        "created_at": "2026-07-15T10:00:00+00:00",
        "warehouse_name": "WH",
        "action": "suspend",
        "reason": "idle",
        "observed_started_clusters": None,
        "observed_resumed_on": None,
        "observed_at": "2026-07-15T09:59:00+00:00",
    }
    del event_row[missing_key]

    def handler(request):
        return httpx.Response(200, json=[event_row])

    with pytest.raises(AutomatedSavingsStoreError) as exc_info:
        _rpc_store(handler).list_events("org-1", limit=1)
    assert exc_info.value.kind == "malformed_row"


def test_read_rpc_http_failure_raises_store_error():
    def handler(request):
        return httpx.Response(503, text="secret body")

    with pytest.raises(AutomatedSavingsStoreError) as exc_info:
        _rpc_store(handler).daily_suspensions("org-1", 7, "2026-07-15")
    assert exc_info.value.kind == "http_status"
    assert exc_info.value.status_code == 503

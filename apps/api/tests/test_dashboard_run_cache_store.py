from datetime import date, datetime, timezone
import json

import httpx

from app.services.dashboard_run_cache import CachedDashboardRun, SupabaseRunCacheStore


def test_supabase_run_cache_store_serializes_dataset_dates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201)

    store = SupabaseRunCacheStore(
        supabase_url="https://project.supabase.co",
        service_role_key="service-role-key",
        transport=httpx.MockTransport(handler),
    )

    store.upsert(
        CachedDashboardRun(
            organization_id="org-1",
            run_id="run-1",
            source="snowflake",
            window_days=90,
            summary={},
            datasets={
                "ai_consumption_daily": [
                    {
                        "usage_date": date(2026, 6, 5),
                        "service_type": "AI_SERVICES",
                        "credits_used": 2.5,
                    }
                ]
            },
            metadata=None,
            source_start_date=date(2026, 6, 5),
            source_end_date=date(2026, 6, 5),
            completed_at=datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
            expires_at=datetime(2026, 7, 7, 12, tzinfo=timezone.utc),
            account_locator="TU24199",
        )
    )

    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["datasets"]["ai_consumption_daily"][0]["usage_date"] == (
        "2026-06-05"
    )


def test_supabase_run_cache_store_updates_datasets_with_current_row_filter() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    store = SupabaseRunCacheStore(
        supabase_url="https://project.supabase.co",
        service_role_key="service-role-key",
        transport=httpx.MockTransport(handler),
    )
    cached = CachedDashboardRun(
        organization_id="org-1",
        run_id="run-1",
        source="snowflake",
        window_days=90,
        summary={},
        datasets={},
        metadata=None,
        source_start_date=date(2026, 6, 5),
        source_end_date=date(2026, 6, 5),
        completed_at=datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 7, 12, tzinfo=timezone.utc),
        account_locator="TU24199",
    )

    store.update_datasets_if_current(
        cached,
        {
            "ai_consumption_daily": [
                {"usage_date": date(2026, 6, 5), "credits_used": 2.5}
            ]
        },
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "PATCH"
    assert request.url.params["organization_id"] == "eq.org-1"
    assert request.url.params["run_id"] == "eq.run-1"
    assert request.url.params["completed_at"] == "eq.2026-07-06T12:00:00+00:00"
    payload = json.loads(request.content)
    assert payload["datasets"]["ai_consumption_daily"][0]["usage_date"] == (
        "2026-06-05"
    )


def test_supabase_run_cache_store_deletes_only_current_row() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    store = SupabaseRunCacheStore(
        supabase_url="https://project.supabase.co",
        service_role_key="service-role-key",
        transport=httpx.MockTransport(handler),
    )
    cached = CachedDashboardRun(
        organization_id="org-1",
        run_id="run-1",
        source="snowflake",
        window_days=90,
        summary={},
        datasets={},
        metadata=None,
        source_start_date=date(2026, 6, 5),
        source_end_date=date(2026, 6, 5),
        completed_at=datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
        expires_at=datetime(2026, 7, 7, 12, tzinfo=timezone.utc),
        account_locator="TU24199",
    )

    store.delete_if_current(cached)

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "DELETE"
    assert request.url.params["organization_id"] == "eq.org-1"
    assert request.url.params["run_id"] == "eq.run-1"
    assert request.url.params["completed_at"] == "eq.2026-07-06T12:00:00+00:00"

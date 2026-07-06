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

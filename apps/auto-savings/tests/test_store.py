from datetime import datetime, timezone

import httpx

from auto_savings.config import WorkerConfig
from auto_savings.store import InMemoryStore, SupabaseStore


def test_in_memory_intent_lifecycle():
    store = InMemoryStore()
    store.write_intent("org-1", "WH1", restore_to=300)
    [intent] = store.list_intents("org-1")
    assert intent.restore_to == 300
    store.delete_intent("org-1", "WH1")
    assert store.list_intents("org-1") == []


def test_supabase_store_writes_intent_via_postgrest():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(201, json=[])

    config = WorkerConfig(supabase_url="https://x.supabase.co", supabase_service_role_key="svc")
    store = SupabaseStore(config, transport=httpx.MockTransport(handler))
    store.write_intent("org-1", "WH1", restore_to=300)

    assert "automated_savings_restore_intents" in seen["url"]
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer svc"

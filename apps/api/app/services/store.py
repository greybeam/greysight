"""Waste-alert dedup store protocol and factory.

The API has no pre-existing Store abstraction (#70's DuckDB store lives on the
auto-savings worker). This protocol is the alert job's persistence surface:
InMemory (tests), Supabase REST (hosted), and DuckDB (optional local file).
"""
from __future__ import annotations

from typing import Protocol

from app.config import Settings
from app.models.waste_alert_state import WasteAlertState


class Store(Protocol):
    async def get_waste_alert_state(
        self, warehouse_name: str
    ) -> WasteAlertState | None: ...

    async def set_waste_alert_state(self, state: WasteAlertState) -> None: ...


def get_store(settings: Settings | None = None) -> Store:
    resolved = settings or Settings()
    if resolved.waste_alert_backend == "supabase":
        from app.services.store_supabase import SupabaseWasteAlertStore

        return SupabaseWasteAlertStore(
            supabase_url=resolved.supabase_url,
            service_role_key=resolved.supabase_service_role_key,
        )
    if resolved.waste_alert_backend == "duckdb":
        from app.services.store_duckdb import DuckDBWasteAlertStore

        return DuckDBWasteAlertStore(path=resolved.waste_alert_duckdb_path)
    from app.services.store_inmemory import InMemoryStore

    return InMemoryStore()

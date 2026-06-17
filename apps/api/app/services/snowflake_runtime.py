from __future__ import annotations

from typing import Callable

from app.config import Settings
from app.services.org_connection_resolver import (
    OrgConnectionRow,
    SupabaseConnectionFetcher,
)


def get_connection_fetcher(
    settings: Settings,
) -> Callable[[str], OrgConnectionRow | None]:
    if (
        settings.auth_required
        and settings.supabase_url.strip()
        and settings.supabase_service_role_key.strip()
    ):
        return SupabaseConnectionFetcher(
            supabase_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
        )
    return lambda _organization_id: None

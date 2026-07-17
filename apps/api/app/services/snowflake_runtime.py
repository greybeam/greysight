from __future__ import annotations

from typing import Callable

from app.config import Settings
from app.services.http_pool import DEFAULT_TIMEOUT_SECONDS, get_sync_client, request_timeout
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
        # Reuse the lifespan-owned pooled sync client so each org-connection
        # lookup rides existing keep-alive connections instead of opening a
        # fresh TLS handshake per request. Resolve the client lazily at request
        # time (never at import) so the pool is already installed.
        return SupabaseConnectionFetcher(
            supabase_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            # Preserve the pooled client's pool-acquisition cap: a scalar
            # timeout would override ``pool`` for every request, so supply the
            # full policy (connect/read/write = DEFAULT_TIMEOUT_SECONDS,
            # pool = POOL_TIMEOUT_SECONDS) alongside the injected client.
            timeout=request_timeout(DEFAULT_TIMEOUT_SECONDS),
            client=get_sync_client(),
        )
    return lambda _organization_id: None

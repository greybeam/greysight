"""Process bootstrap for the automated-savings worker.

Wires the real ``SupabaseStore``, a bounded thread pool, and a lazy per-tenant
``TenantSession`` factory, then runs the ``supervisor`` forever. ``run()`` is the
synchronous entrypoint invoked by ``dev.py`` (which loads the local ``.env``
before importing this module).
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from greysight_connect.org_connection_resolver import (
    SupabaseConnectionFetcher,
    resolve_snowflake_config,
)

from auto_savings.config import WorkerConfig
from auto_savings.snowflake_session import TenantSession, connection_fingerprint
from auto_savings.store import SupabaseStore
from auto_savings.tenant_loop import supervisor


def _require_supabase_credentials(config: WorkerConfig) -> None:
    """Fail fast if Supabase creds are missing, instead of making doomed requests."""
    missing = [
        name
        for name, value in (
            ("SUPABASE_URL", config.supabase_url),
            ("SUPABASE_SERVICE_ROLE_KEY", config.supabase_service_role_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )


async def main() -> None:
    """Build the worker's dependencies and run the supervisor forever."""
    config = WorkerConfig.from_environment()
    _require_supabase_credentials(config)
    store = SupabaseStore(config, timeout_seconds=config.store_timeout_seconds)
    fetch_connection = SupabaseConnectionFetcher(
        supabase_url=config.supabase_url,
        service_role_key=config.supabase_service_role_key,
    )

    def session_factory(org_id: str) -> tuple[TenantSession, str]:
        # Resolve each tenant's Snowflake config lazily, on first enrollment.
        # Derive BOTH the warm session AND its fingerprint from this SINGLE
        # resolve, so the session and the fingerprint it is compared against can
        # never disagree (a rotation between two separate resolves would pin the
        # old session to the new fingerprint forever — finding #2).
        snowflake_config = resolve_snowflake_config(
            org_id, config, fetch_connection=fetch_connection
        )
        session = TenantSession(
            config=snowflake_config,
            socket_timeout_seconds=config.socket_timeout_seconds,
        )
        return session, connection_fingerprint(snowflake_config)

    def fingerprint_fn(org_id: str) -> str:
        # Re-resolve on each refresh so a disconnected/rotated org is detected:
        # a changed fingerprint recycles the warm session, and an
        # OrgConnectionNotConfiguredError (propagated) drops it.
        snowflake_config = resolve_snowflake_config(
            org_id, config, fetch_connection=fetch_connection
        )
        return connection_fingerprint(snowflake_config)

    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        await supervisor(
            store=store,
            config=config,
            executor=executor,
            session_factory=session_factory,
            fingerprint_fn=fingerprint_fn,
        )


def run() -> None:
    """Synchronous entrypoint (used by ``dev.py``)."""
    asyncio.run(main())


if __name__ == "__main__":
    run()

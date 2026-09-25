"""Process bootstrap for the automated-savings worker.

Wires a ``Store``, a bounded thread pool, and a lazy per-tenant
``TenantSession`` factory, then runs the ``supervisor`` forever. ``run()`` is the
synchronous entrypoint invoked by ``dev.py`` (which loads the local ``.env``
before importing this module). Default backend is Supabase; DuckDB is the
single-tenant local path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from greysight_connect.org_connection_resolver import (
    SupabaseConnectionFetcher,
    resolve_snowflake_config,
)
from greysight_connect.snowflake_client import SnowflakeConnectionConfig

from auto_savings.config import WorkerConfig
from auto_savings.local_store import LOCAL_ORG_ID, LocalDuckDBStore
from auto_savings.snowflake_session import TenantSession, connection_fingerprint
from auto_savings.store import Store, SupabaseStore
from auto_savings.tenant_loop import supervisor

SessionFactory = Callable[[str], tuple[TenantSession, str]]
FingerprintFn = Callable[[str], str]


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


def _build_supabase_store(
    config: WorkerConfig,
) -> tuple[Store, SessionFactory, FingerprintFn]:
    _require_supabase_credentials(config)
    store: Store = SupabaseStore(
        config, timeout_seconds=config.store_timeout_seconds
    )
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

    return store, session_factory, fingerprint_fn


def _build_local_store(
    config: WorkerConfig,
) -> tuple[Store, SessionFactory, FingerprintFn]:
    assert config.duckdb_path is not None  # enforced in WorkerConfig
    store: Store = LocalDuckDBStore(config.duckdb_path)
    snowflake_config = SnowflakeConnectionConfig.from_environment()

    def session_factory(org_id: str) -> tuple[TenantSession, str]:
        # Local mode is single-tenant; every enrolled tenant IS
        # LOCAL_ORG_ID. Fail loud if the supervisor ever hands us a foreign
        # org — it means ``worker_tenants`` was widened without the
        # resolver keeping up.
        if org_id != LOCAL_ORG_ID:
            raise RuntimeError(
                f"local backend can only resolve {LOCAL_ORG_ID!r}, "
                f"got {org_id!r}"
            )
        session = TenantSession(
            config=snowflake_config,
            socket_timeout_seconds=config.socket_timeout_seconds,
        )
        return session, connection_fingerprint(snowflake_config)

    def fingerprint_fn(org_id: str) -> str:
        if org_id != LOCAL_ORG_ID:
            raise RuntimeError(
                f"local backend can only resolve {LOCAL_ORG_ID!r}, "
                f"got {org_id!r}"
            )
        return connection_fingerprint(snowflake_config)

    return store, session_factory, fingerprint_fn


async def main() -> None:
    """Build the worker's dependencies and run the supervisor forever."""
    config = WorkerConfig.from_environment()
    if config.backend == "duckdb":
        store, session_factory, fingerprint_fn = _build_local_store(config)
    else:
        store, session_factory, fingerprint_fn = _build_supabase_store(config)

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

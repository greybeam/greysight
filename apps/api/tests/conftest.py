import contextlib

import anyio
import httpx
import pytest

from app.services import auth_cache, query_concurrency
from app.services.http_pool import clear_clients, install_clients

# Configuration globals reset() deliberately does NOT touch.
_AUTH_CACHE_CONFIG_GLOBALS = (
    "_ttl_seconds",
    "_verify_cache",
    "_membership_cache",
    "_monotonic_clock",
    "_wall_clock",
)


@pytest.fixture
def anyio_backend() -> str:
    """Pin anyio's pytest plugin to a single backend.

    The plugin's own ``anyio_backend`` fixture is parametrised over every
    installed backend, which would both duplicate test ids and require trio.
    Only asyncio is installed here, and the auth cache is asyncio-specific
    (``asyncio.Task``/``asyncio.shield``), so pin it.
    """
    return "asyncio"


@contextlib.contextmanager
def installed_sync_pool(handler):
    """Install a pooled sync client backed by ``handler`` for the block.

    Yields the sync client so tests can assert it is reused (not closed) by
    service clients that go through the shared HTTP pool.
    """
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


@pytest.fixture(autouse=True)
def _restore_default_query_executor():
    """Restore the process-wide query executor to its default after each test.

    Several tests call ``query_concurrency.configure(...)`` which swaps the
    module-level singleton. Without this, a test that shrinks the worker cap
    would leak that config into unrelated tests (order-dependent flakes).
    """
    yield
    query_concurrency.configure(query_concurrency.DEFAULT_MAX_WORKERS)


@pytest.fixture(autouse=True)
def _reset_auth_cache():
    """Clear cached entries AND restore configuration between tests.

    reset() preserves the configured TTL and clocks by design, so a test that
    installs a fake clock through configure_auth_cache would otherwise leak
    frozen time into every test that runs after it.

    Snapshot-and-restore rather than reconfigure: calling
    configure_auth_cache(Settings()) here would leave the caches non-None no
    matter what, which would make the module-scope wiring assertions in
    test_auth_cache_wiring.py pass even if app.main forgot to configure the
    cache at all. Restoring the snapshot preserves exactly what import-time
    wiring left behind — including None, if it left nothing.
    """
    snapshot = {name: getattr(auth_cache, name) for name in _AUTH_CACHE_CONFIG_GLOBALS}
    anyio.run(auth_cache.reset)
    try:
        yield
    finally:
        anyio.run(auth_cache.reset)
        for name, value in snapshot.items():
            setattr(auth_cache, name, value)

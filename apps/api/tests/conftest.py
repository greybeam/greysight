import contextlib

import anyio
import httpx
import pytest

from app.services import query_concurrency
from app.services.http_pool import clear_clients, install_clients


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

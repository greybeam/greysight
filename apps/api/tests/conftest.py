import pytest

from app.services import query_concurrency


@pytest.fixture(autouse=True)
def _restore_default_query_executor():
    """Restore the process-wide query executor to its default after each test.

    Several tests call ``query_concurrency.configure(...)`` which swaps the
    module-level singleton. Without this, a test that shrinks the worker cap
    would leak that config into unrelated tests (order-dependent flakes).
    """
    yield
    query_concurrency.configure(query_concurrency.DEFAULT_MAX_WORKERS)

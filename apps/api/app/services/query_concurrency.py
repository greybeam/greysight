from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

DEFAULT_MAX_WORKERS = 8
_executor = ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS)


def configure(max_workers: int) -> None:
    """Rebuild the process-wide query executor. Call once at app startup."""
    global _executor
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    old_executor = _executor
    _executor = ThreadPoolExecutor(max_workers=max_workers)
    old_executor.shutdown(wait=False, cancel_futures=True)


def get_query_executor() -> ThreadPoolExecutor:
    return _executor

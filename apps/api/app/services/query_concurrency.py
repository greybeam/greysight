from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

DEFAULT_MAX_WORKERS = 8
_lock = Lock()
_executor = ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS)


def configure(max_workers: int) -> None:
    """Rebuild the process-wide query executor. Call once at app startup.

    Guarded by ``_lock`` so a concurrent ``configure``/``get_query_executor``
    can never observe or swap a half-initialized singleton (TOCTOU).
    """
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    global _executor
    with _lock:
        old_executor = _executor
        _executor = ThreadPoolExecutor(max_workers=max_workers)
    old_executor.shutdown(wait=False, cancel_futures=True)


def get_query_executor() -> ThreadPoolExecutor:
    with _lock:
        return _executor

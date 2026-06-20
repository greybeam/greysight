from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

DEFAULT_MAX_WORKERS = 8
_lock = Lock()
_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(
    max_workers=DEFAULT_MAX_WORKERS
)


def configure(max_workers: int) -> None:
    """Rebuild the process-wide query executor. Call once at app startup.

    Guarded by ``_lock`` so a concurrent ``configure``/``get_query_executor``
    can never observe or swap a half-initialized singleton (TOCTOU). Safe to
    call after ``shutdown`` left the singleton torn down, which is how a
    restarted app object (same process) revives the executor.
    """
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    global _executor
    with _lock:
        old_executor = _executor
        _executor = ThreadPoolExecutor(max_workers=max_workers)
    if old_executor is not None:
        old_executor.shutdown(wait=False, cancel_futures=True)


def get_query_executor() -> ThreadPoolExecutor:
    """Return the process-wide query executor, reviving it if it was shut down.

    A prior ``shutdown`` leaves the singleton torn down; lazily recreating a
    default executor here keeps query scheduling working if the app object is
    restarted in the same process without an explicit ``configure``.
    """
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS)
        return _executor


def shutdown(cancel_futures: bool = True) -> None:
    """Shut down the process-wide query executor.

    Call once during app shutdown so queued query work does not outlive the
    process (e.g. on reload). The singleton is reset to ``None`` so a later
    ``configure``/``get_query_executor`` recreates it instead of handing back a
    dead executor that rejects new work. Guarded by ``_lock`` to stay consistent
    with ``configure``/``get_query_executor``.
    """
    global _executor
    with _lock:
        executor = _executor
        _executor = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=cancel_futures)

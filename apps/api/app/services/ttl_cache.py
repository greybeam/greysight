from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Hashable


class TtlCache:
    """Small bounded TTL cache for per-org API responses.

    Thread-safe; entries expire ttl_seconds after their last set(), and the
    oldest-inserted entry is evicted once max_entries is reached. The clock is
    injectable so tests never sleep.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._clock() >= expires_at:
                del self._entries[key]
                return None
            return value

    def set(
        self, key: Hashable, value: Any, *, expires_at: float | None = None
    ) -> None:
        """Store a value, expiring it ttl_seconds from now by default.

        ``expires_at`` overrides that with an absolute deadline on this
        cache's clock. It exists for callers whose value was really earned
        earlier than the moment they publish it — an entry filled from a slow
        upstream fetch must expire relative to when the fetch *started*, or
        its freshness window silently becomes TTL plus the fetch duration.
        Insertion order, and therefore FIFO eviction, is unaffected.
        """
        with self._lock:
            self._entries.pop(key, None)
            deadline = (
                self._clock() + self._ttl_seconds if expires_at is None else expires_at
            )
            self._entries[key] = (deadline, value)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def delete(self, key: Hashable) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

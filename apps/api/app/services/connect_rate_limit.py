"""In-process only; swap for a Postgres/Redis store if running multiple API instances."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Callable


class ConnectRateLimitError(RuntimeError):
    """Base class for connect throttling errors."""


class ConnectRateLimitedError(ConnectRateLimitError):
    """Raised when a user exceeds the rolling-window connect attempt limit."""


class ConnectInFlightError(ConnectRateLimitError):
    """Raised when a user already has an in-flight connect attempt."""


class InMemoryConnectLimiter:
    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._now = now
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}
        self._in_flight: set[str] = set()

    @contextmanager
    def guard(self, user_id: str) -> Iterator[None]:
        with self._lock:
            if user_id in self._in_flight:
                raise ConnectInFlightError(
                    "A connection attempt is already in progress."
                )

            now = self._now()
            window_start = now - self._window_seconds
            recent = [t for t in self._attempts.get(user_id, []) if t > window_start]
            if len(recent) >= self._max_attempts:
                # Reject WITHOUT registering an in-flight key we could not clear.
                self._attempts[user_id] = recent
                raise ConnectRateLimitedError(
                    "Too many connection attempts. Try again shortly."
                )

            recent.append(now)
            self._attempts[user_id] = recent
            self._in_flight.add(user_id)

        try:
            yield
        finally:
            with self._lock:
                self._in_flight.discard(user_id)


_limiter: InMemoryConnectLimiter | None = None

DEFAULT_MAX_ATTEMPTS = 10
DEFAULT_WINDOW_SECONDS = 300.0


def get_connect_limiter() -> InMemoryConnectLimiter:
    global _limiter
    if _limiter is None:
        _limiter = InMemoryConnectLimiter(
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            window_seconds=DEFAULT_WINDOW_SECONDS,
        )
    return _limiter


_invite_limiter: InMemoryConnectLimiter | None = None

DEFAULT_INVITE_MAX_ATTEMPTS = 20


def get_invite_limiter() -> InMemoryConnectLimiter:
    """Separate limiter so invites and Snowflake connects don't share in-flight
    state for the same user."""
    global _invite_limiter
    if _invite_limiter is None:
        _invite_limiter = InMemoryConnectLimiter(
            max_attempts=DEFAULT_INVITE_MAX_ATTEMPTS,
            window_seconds=DEFAULT_WINDOW_SECONDS,
        )
    return _invite_limiter

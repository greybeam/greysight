import pytest

from app.services.connect_rate_limit import (
    ConnectRateLimitedError,
    ConnectInFlightError,
    InMemoryConnectLimiter,
)


def test_allows_up_to_limit_then_blocks() -> None:
    clock = {"t": 1000.0}
    limiter = InMemoryConnectLimiter(
        max_attempts=3, window_seconds=60, now=lambda: clock["t"]
    )
    for _ in range(3):
        with limiter.guard("user-1"):
            pass
    with pytest.raises(ConnectRateLimitedError):
        with limiter.guard("user-1"):
            pass


def test_window_resets_after_expiry() -> None:
    clock = {"t": 1000.0}
    limiter = InMemoryConnectLimiter(
        max_attempts=1, window_seconds=60, now=lambda: clock["t"]
    )
    with limiter.guard("user-1"):
        pass
    clock["t"] += 61
    with limiter.guard("user-1"):  # no raise — window rolled over
        pass


def test_rejects_concurrent_in_flight_for_same_user() -> None:
    limiter = InMemoryConnectLimiter(max_attempts=10, window_seconds=60)
    with limiter.guard("user-1"):
        with pytest.raises(ConnectInFlightError):
            with limiter.guard("user-1"):
                pass


def test_per_user_isolation() -> None:
    limiter = InMemoryConnectLimiter(max_attempts=1, window_seconds=60)
    with limiter.guard("user-1"):
        pass
    with limiter.guard("user-2"):  # different user unaffected
        pass


def test_in_flight_released_when_body_raises() -> None:
    limiter = InMemoryConnectLimiter(max_attempts=10, window_seconds=60)
    with pytest.raises(ValueError):
        with limiter.guard("user-1"):
            raise ValueError("boom")
    # The failed attempt must NOT leave the user permanently in-flight.
    with limiter.guard("user-1"):
        pass

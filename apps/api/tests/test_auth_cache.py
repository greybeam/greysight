import asyncio
import base64
import json

import anyio
import pytest

from app.config import Settings
from app.services import auth_cache
from app.services.auth_cache import VerifiedIdentity


class FakeClock:
    """Injectable monotonic clock — tests advance time instead of sleeping."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeWallClock:
    """Injectable wall clock, in epoch seconds."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def configure(
    ttl: float,
    *,
    monotonic: FakeClock | None = None,
    wall: FakeWallClock | None = None,
) -> tuple[FakeClock, FakeWallClock]:
    monotonic_clock = monotonic or FakeClock()
    wall_clock = wall or FakeWallClock()
    auth_cache.configure_auth_cache(
        Settings(GREYSIGHT_AUTH_CACHE_TTL_SECONDS=ttl),
        monotonic_clock=monotonic_clock,
        wall_clock=wall_clock,
    )
    return monotonic_clock, wall_clock


def make_verifier(user_id: str = "user_123") -> tuple[list[int], object]:
    """A counting fetch closure returning a VerifiedIdentity."""
    calls: list[int] = []

    async def fetch() -> VerifiedIdentity:
        calls.append(1)
        return VerifiedIdentity(user_id=user_id)

    return calls, fetch


def test_verify_hit_within_ttl_makes_one_upstream_call() -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_verifier()

    async def scenario() -> None:
        assert (await auth_cache.cached_verify("token-a", fetch)).user_id == "user_123"
        clock.now = 29.0
        assert (await auth_cache.cached_verify("token-a", fetch)).user_id == "user_123"

    anyio.run(scenario)

    assert len(calls) == 1


def test_verify_refetches_after_ttl_expiry() -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_verifier()

    async def scenario() -> None:
        await auth_cache.cached_verify("token-a", fetch)
        clock.now = 30.1
        await auth_cache.cached_verify("token-a", fetch)

    anyio.run(scenario)

    assert len(calls) == 2


def test_distinct_tokens_do_not_cross_contaminate() -> None:
    configure(30.0)

    async def scenario() -> None:
        async def fetch_a() -> VerifiedIdentity:
            return VerifiedIdentity(user_id="user_a")

        async def fetch_b() -> VerifiedIdentity:
            return VerifiedIdentity(user_id="user_b")

        assert (await auth_cache.cached_verify("token-a", fetch_a)).user_id == "user_a"
        assert (await auth_cache.cached_verify("token-b", fetch_b)).user_id == "user_b"
        assert (await auth_cache.cached_verify("token-a", fetch_b)).user_id == "user_a"

    anyio.run(scenario)


def test_failed_verification_is_never_cached_and_a_later_success_populates() -> None:
    configure(30.0)
    calls: list[int] = []

    async def scenario() -> None:
        async def failing() -> VerifiedIdentity:
            calls.append(1)
            raise RuntimeError("upstream said no")

        with pytest.raises(RuntimeError):
            await auth_cache.cached_verify("token-a", failing)
        with pytest.raises(RuntimeError):
            await auth_cache.cached_verify("token-a", failing)
        assert len(calls) == 2

        async def succeeding() -> VerifiedIdentity:
            calls.append(1)
            return VerifiedIdentity(user_id="user_123")

        await auth_cache.cached_verify("token-a", succeeding)
        await auth_cache.cached_verify("token-a", succeeding)

    anyio.run(scenario)

    assert len(calls) == 3


def test_ttl_zero_disables_verification_storage_and_coalescing() -> None:
    """Disabled means passthrough: no storage AND no single-flight.

    The concurrent half is the load-bearing assertion — a sequential pair
    would also pass if TTL=0 merely skipped storage while still collapsing
    concurrent callers onto one shared fetch.
    """
    configure(0.0)
    calls: list[int] = []

    async def scenario() -> None:
        gate = asyncio.Event()

        async def fetch() -> VerifiedIdentity:
            calls.append(1)
            await gate.wait()
            return VerifiedIdentity(user_id="user_123")

        waiters = [
            asyncio.ensure_future(auth_cache.cached_verify("token-a", fetch))
            for _ in range(4)
        ]
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(*waiters)

        # Sequential calls also each reach the fetch.
        await auth_cache.cached_verify("token-a", fetch)

    anyio.run(scenario)

    assert len(calls) == 5


# NOTE: the concurrent half of the test above passes trivially at this task —
# single-flight does not exist yet. It is written now so Task 6 cannot regress
# it; Task 6 adds the matching "registers nothing" assertion.


def test_reset_clears_entries_but_preserves_the_configured_ttl() -> None:
    clock, _ = configure(45.0)
    calls, fetch = make_verifier()

    async def scenario() -> None:
        await auth_cache.cached_verify("token-a", fetch)
        await auth_cache.reset()
        await auth_cache.cached_verify("token-a", fetch)
        clock.now = 44.0
        await auth_cache.cached_verify("token-a", fetch)

    anyio.run(scenario)

    # 2 = one before reset, one after; the third call is a hit because the
    # 45s TTL survived reset().
    assert len(calls) == 2


def test_token_is_never_used_as_a_cache_key() -> None:
    configure(30.0)
    _, fetch = make_verifier()

    async def scenario() -> None:
        await auth_cache.cached_verify("super-secret-token", fetch)

    anyio.run(scenario)

    keys = list(auth_cache._verify_cache._entries)  # noqa: SLF001 — key-shape check
    assert "super-secret-token" not in keys
    assert all(len(key) == 64 for key in keys)


def jwt_with_exp(exp: object) -> str:
    """A structurally valid JWT whose payload carries the given exp."""

    def segment(payload: dict[str, object]) -> str:
        raw = json.dumps(payload).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{segment({'alg': 'HS256'})}.{segment({'sub': 'user_123', 'exp': exp})}.sig"


def test_token_exp_before_the_ttl_boundary_stops_being_served_at_exp() -> None:
    clock, wall = configure(30.0)
    calls, fetch = make_verifier()
    # exp is 10s of wall-clock away, well inside the 30s TTL.
    token = jwt_with_exp(wall.now + 10.0)

    async def scenario() -> None:
        await auth_cache.cached_verify(token, fetch)
        clock.now = 9.9
        await auth_cache.cached_verify(token, fetch)
        assert len(calls) == 1
        clock.now = 10.0
        await auth_cache.cached_verify(token, fetch)

    anyio.run(scenario)

    assert len(calls) == 2


def test_a_wall_clock_jump_does_not_extend_acceptance() -> None:
    clock, wall = configure(30.0)
    calls, fetch = make_verifier()
    token = jwt_with_exp(wall.now + 10.0)

    async def scenario() -> None:
        await auth_cache.cached_verify(token, fetch)
        # Wall clock jumps backwards an hour mid-TTL; the deadline is
        # monotonic-only, so it cannot buy the entry extra life.
        wall.now -= 3600.0
        clock.now = 10.0
        await auth_cache.cached_verify(token, fetch)

    anyio.run(scenario)

    assert len(calls) == 2


def test_an_already_expired_token_is_verified_but_never_cached() -> None:
    _, wall = configure(30.0)
    calls, fetch = make_verifier()
    token = jwt_with_exp(wall.now - 5.0)

    async def scenario() -> None:
        await auth_cache.cached_verify(token, fetch)
        await auth_cache.cached_verify(token, fetch)

    anyio.run(scenario)

    assert len(calls) == 2


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        "only.two",
        "aaa.!!!not-base64!!!.sig",
    ],
)
def test_unparseable_tokens_fall_back_to_the_plain_ttl(token: str) -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_verifier()

    async def scenario() -> None:
        await auth_cache.cached_verify(token, fetch)
        clock.now = 29.0
        await auth_cache.cached_verify(token, fetch)

    anyio.run(scenario)

    assert len(calls) == 1


@pytest.mark.parametrize("exp", ["soon", None, True])
def test_non_numeric_exp_falls_back_to_the_plain_ttl(exp: object) -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_verifier()
    token = jwt_with_exp(exp)

    async def scenario() -> None:
        await auth_cache.cached_verify(token, fetch)
        clock.now = 29.0
        await auth_cache.cached_verify(token, fetch)

    anyio.run(scenario)

    assert len(calls) == 1

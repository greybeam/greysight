import asyncio
import base64
import hashlib
import json

import anyio
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services import auth_cache
from app.services.auth_cache import VerifiedIdentity
from app.services.membership_directory import (
    MembershipLookupError,
    MembershipLookupUnavailable,
    Organization,
)


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


ORG_A = Organization(id="org-a", name="Acme")
ORG_B = Organization(id="org-b", name="Beta")


def make_lookup(*orgs: Organization) -> tuple[list[int], object]:
    calls: list[int] = []

    async def fetch() -> tuple[Organization, ...]:
        calls.append(1)
        return orgs

    return calls, fetch


def test_membership_hit_within_ttl_makes_one_upstream_call() -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_lookup(ORG_A)

    async def scenario() -> None:
        assert await auth_cache.cached_memberships("user_1", fetch) == (ORG_A,)
        clock.now = 29.0
        assert await auth_cache.cached_memberships("user_1", fetch) == (ORG_A,)

    anyio.run(scenario)

    assert len(calls) == 1


def test_membership_refetches_after_ttl_expiry() -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_lookup(ORG_A)

    async def scenario() -> None:
        await auth_cache.cached_memberships("user_1", fetch)
        clock.now = 30.1
        await auth_cache.cached_memberships("user_1", fetch)

    anyio.run(scenario)

    assert len(calls) == 2


def test_distinct_users_do_not_cross_contaminate() -> None:
    configure(30.0)

    async def scenario() -> None:
        _, fetch_a = make_lookup(ORG_A)
        _, fetch_b = make_lookup(ORG_B)
        assert await auth_cache.cached_memberships("user_1", fetch_a) == (ORG_A,)
        assert await auth_cache.cached_memberships("user_2", fetch_b) == (ORG_B,)
        assert await auth_cache.cached_memberships("user_1", fetch_b) == (ORG_A,)

    anyio.run(scenario)


def test_failed_membership_lookup_is_never_cached() -> None:
    configure(30.0)
    calls: list[int] = []

    async def scenario() -> None:
        async def failing() -> tuple[Organization, ...]:
            calls.append(1)
            raise RuntimeError("upstream said no")

        for _ in range(2):
            with pytest.raises(RuntimeError):
                await auth_cache.cached_memberships("user_1", failing)

    anyio.run(scenario)

    assert len(calls) == 2


def test_ttl_zero_disables_membership_storage_and_coalescing() -> None:
    """Concurrent callers must each reach the fetch, not share one."""
    configure(0.0)
    calls: list[int] = []

    async def scenario() -> None:
        gate = asyncio.Event()

        async def fetch() -> tuple[Organization, ...]:
            calls.append(1)
            await gate.wait()
            return (ORG_A,)

        waiters = [
            asyncio.ensure_future(auth_cache.cached_memberships("user_1", fetch))
            for _ in range(4)
        ]
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(*waiters)

        await auth_cache.cached_memberships("user_1", fetch)

    anyio.run(scenario)

    assert len(calls) == 5


def test_invalidate_user_forces_a_refetch_for_that_user_only() -> None:
    configure(30.0)
    calls_1, fetch_1 = make_lookup(ORG_A)
    calls_2, fetch_2 = make_lookup(ORG_B)

    async def scenario() -> None:
        await auth_cache.cached_memberships("user_1", fetch_1)
        await auth_cache.cached_memberships("user_2", fetch_2)
        auth_cache.invalidate_user("user_1")
        await auth_cache.cached_memberships("user_1", fetch_1)
        await auth_cache.cached_memberships("user_2", fetch_2)

    anyio.run(scenario)

    assert len(calls_1) == 2
    assert len(calls_2) == 1


def test_invalidate_all_memberships_forces_a_refetch_for_everyone() -> None:
    configure(30.0)
    calls_1, fetch_1 = make_lookup(ORG_A)
    calls_2, fetch_2 = make_lookup(ORG_B)

    async def scenario() -> None:
        await auth_cache.cached_memberships("user_1", fetch_1)
        await auth_cache.cached_memberships("user_2", fetch_2)
        auth_cache.invalidate_all_memberships()
        await auth_cache.cached_memberships("user_1", fetch_1)
        await auth_cache.cached_memberships("user_2", fetch_2)

    anyio.run(scenario)

    assert len(calls_1) == 2
    assert len(calls_2) == 2


@pytest.mark.parametrize(
    "invalidate",
    [
        lambda: auth_cache.invalidate_user("user_1"),
        lambda: auth_cache.invalidate_all_memberships(),
    ],
    ids=["invalidate_user", "invalidate_all"],
)
def test_invalidation_mid_fetch_does_not_publish_the_stale_value(invalidate) -> None:
    """The classic race: fetch starts, invalidation lands, fetch returns stale."""
    configure(30.0)
    results = [(ORG_A,), (ORG_B,)]
    calls: list[int] = []

    async def scenario() -> None:
        async def fetch() -> tuple[Organization, ...]:
            calls.append(1)
            if len(calls) == 1:
                # Invalidate while this first (now-stale) fetch is in flight.
                invalidate()
            return results[min(len(calls), len(results)) - 1]

        # The stale first result is returned to this caller but NOT cached,
        # and a single bounded refetch supplies the value that is returned.
        assert await auth_cache.cached_memberships("user_1", fetch) == (ORG_B,)
        assert len(calls) == 2

        # Nothing was published, so the next call fetches again.
        assert await auth_cache.cached_memberships("user_1", fetch) == (ORG_B,)
        assert len(calls) == 3

    anyio.run(scenario)


def test_reset_zeroes_the_generation_counter() -> None:
    configure(30.0)
    auth_cache.invalidate_all_memberships()
    assert auth_cache._membership_generation > 0  # noqa: SLF001 — state assertion

    anyio.run(auth_cache.reset)

    assert auth_cache._membership_generation == 0  # noqa: SLF001


def test_concurrent_same_token_requests_collapse_to_one_upstream_call() -> None:
    configure(30.0)
    calls: list[int] = []

    async def scenario() -> None:
        gate = asyncio.Event()

        async def fetch() -> VerifiedIdentity:
            calls.append(1)
            await gate.wait()
            return VerifiedIdentity(user_id="user_123")

        waiters = [
            asyncio.ensure_future(auth_cache.cached_verify("token-a", fetch))
            for _ in range(5)
        ]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*waiters)

        assert [r.user_id for r in results] == ["user_123"] * 5

    anyio.run(scenario)

    assert len(calls) == 1


def test_concurrent_same_user_membership_requests_collapse() -> None:
    configure(30.0)
    calls: list[int] = []

    async def scenario() -> None:
        gate = asyncio.Event()

        async def fetch() -> tuple[Organization, ...]:
            calls.append(1)
            await gate.wait()
            return (ORG_A,)

        waiters = [
            asyncio.ensure_future(auth_cache.cached_memberships("user_1", fetch))
            for _ in range(5)
        ]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*waiters)

        assert results == [(ORG_A,)] * 5

    anyio.run(scenario)

    assert len(calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        HTTPException(status_code=401, detail="Authentication required"),
        HTTPException(status_code=503, detail="Authentication service unavailable"),
    ],
    ids=["401", "503"],
)
def test_every_waiter_on_a_failing_verify_receives_the_same_status(error) -> None:
    configure(30.0)

    async def scenario() -> None:
        gate = asyncio.Event()

        async def fetch() -> VerifiedIdentity:
            await gate.wait()
            raise error

        waiters = [
            asyncio.ensure_future(auth_cache.cached_verify("token-a", fetch))
            for _ in range(4)
        ]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*waiters, return_exceptions=True)

        assert [r.status_code for r in results] == [error.status_code] * 4

    anyio.run(scenario)


@pytest.mark.parametrize(
    "error_factory,expected",
    [
        (MembershipLookupError, MembershipLookupError),
        (MembershipLookupUnavailable, MembershipLookupUnavailable),
    ],
    ids=["lookup-error", "lookup-unavailable"],
)
def test_every_waiter_on_a_failing_membership_fetch_sees_the_same_error(
    error_factory, expected
) -> None:
    """Unit-level fan-out only — this fetch sits BELOW _fetch_organizations.

    The 401/503 translation these errors receive on the production path is
    covered by test_concurrent_waiters_get_the_same_translated_membership_failure
    in tests/test_auth.py (Task 8); do not mistake this for that coverage.
    """
    configure(30.0)

    async def scenario() -> None:
        gate = asyncio.Event()

        async def fetch() -> tuple[Organization, ...]:
            await gate.wait()
            raise error_factory()

        waiters = [
            asyncio.ensure_future(auth_cache.cached_memberships("user_1", fetch))
            for _ in range(4)
        ]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*waiters, return_exceptions=True)

        assert all(isinstance(r, expected) for r in results)

    anyio.run(scenario)


def test_cancelling_the_leader_waiter_does_not_break_the_other_waiters() -> None:
    configure(30.0)
    calls: list[int] = []

    async def scenario() -> None:
        gate = asyncio.Event()

        async def fetch() -> VerifiedIdentity:
            calls.append(1)
            await gate.wait()
            return VerifiedIdentity(user_id="user_123")

        leader = asyncio.ensure_future(auth_cache.cached_verify("token-a", fetch))
        follower = asyncio.ensure_future(auth_cache.cached_verify("token-a", fetch))
        await asyncio.sleep(0)

        leader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leader

        gate.set()
        # The shielded underlying task survived its cancelled waiter.
        assert (await follower).user_id == "user_123"

        # A later retry is served from the cache the surviving task populated.
        assert (await auth_cache.cached_verify("token-a", fetch)).user_id == "user_123"

    anyio.run(scenario)

    assert len(calls) == 1


def test_a_fully_cancelled_flight_still_allows_a_successful_retry() -> None:
    configure(30.0)
    calls: list[int] = []

    async def scenario() -> None:
        gate = asyncio.Event()

        async def failing() -> VerifiedIdentity:
            calls.append(1)
            await gate.wait()
            raise HTTPException(status_code=503, detail="unavailable")

        waiter = asyncio.ensure_future(auth_cache.cached_verify("token-a", failing))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        async def succeeding() -> VerifiedIdentity:
            calls.append(1)
            return VerifiedIdentity(user_id="user_123")

        assert (
            await auth_cache.cached_verify("token-a", succeeding)
        ).user_id == "user_123"

    anyio.run(scenario)

    assert len(calls) == 2


def test_ttl_zero_registers_nothing_in_either_registry() -> None:
    """Disabled short-circuits BEFORE hashing, locking, or registering work."""
    configure(0.0)

    async def scenario() -> None:
        gate = asyncio.Event()

        async def verify_fetch() -> VerifiedIdentity:
            await gate.wait()
            return VerifiedIdentity(user_id="user_123")

        async def membership_fetch() -> tuple[Organization, ...]:
            await gate.wait()
            return (ORG_A,)

        waiters = [
            asyncio.ensure_future(auth_cache.cached_verify("token-a", verify_fetch)),
            asyncio.ensure_future(
                auth_cache.cached_memberships("user_1", membership_fetch)
            ),
        ]
        await asyncio.sleep(0)

        assert auth_cache._verify_inflight == {}  # noqa: SLF001 — state assertion
        assert auth_cache._membership_inflight == {}  # noqa: SLF001

        gate.set()
        await asyncio.gather(*waiters)

    anyio.run(scenario)


def test_verify_and_membership_single_flight_are_isolated() -> None:
    """A colliding key in the other key space must never be served across."""
    configure(30.0)
    # Use the sha256 of a token as a *user id*, so a single shared registry
    # keyed by bare strings would hand one caller the other's value.
    token = "token-a"
    colliding_user_id = hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def scenario() -> None:
        gate = asyncio.Event()

        async def verify_fetch() -> VerifiedIdentity:
            await gate.wait()
            return VerifiedIdentity(user_id="user_from_verify")

        async def membership_fetch() -> tuple[Organization, ...]:
            await gate.wait()
            return (ORG_B,)

        verify_waiter = asyncio.ensure_future(
            auth_cache.cached_verify(token, verify_fetch)
        )
        membership_waiter = asyncio.ensure_future(
            auth_cache.cached_memberships(colliding_user_id, membership_fetch)
        )
        await asyncio.sleep(0)
        gate.set()

        identity = await verify_waiter
        organizations = await membership_waiter

        assert identity.user_id == "user_from_verify"
        assert organizations == (ORG_B,)

    anyio.run(scenario)


def test_reset_cancels_and_drains_in_flight_tasks() -> None:
    configure(30.0)

    async def scenario() -> None:
        gate = asyncio.Event()
        observed: list[str] = []

        async def fetch() -> VerifiedIdentity:
            try:
                await gate.wait()
            except asyncio.CancelledError:
                observed.append("cancelled")
                raise
            return VerifiedIdentity(user_id="user_123")

        waiter = asyncio.ensure_future(auth_cache.cached_verify("token-a", fetch))
        # Two yields, not one: the first only lets cached_verify register the
        # single-flight task, the second lets that task actually enter fetch()
        # and block on the gate. Cancelling a task that has not started yet
        # would never reach the fetch's CancelledError handler.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert auth_cache._verify_inflight  # noqa: SLF001 — state assertion

        await auth_cache.reset()

        assert observed == ["cancelled"]
        assert auth_cache._verify_inflight == {}  # noqa: SLF001
        assert auth_cache._membership_inflight == {}  # noqa: SLF001

        with pytest.raises(asyncio.CancelledError):
            await waiter

    anyio.run(scenario)


def test_reset_drains_a_membership_flight_and_leaves_no_pending_task() -> None:
    configure(30.0)

    async def scenario() -> None:
        gate = asyncio.Event()

        async def fetch() -> tuple[Organization, ...]:
            await gate.wait()
            return (ORG_A,)

        waiter = asyncio.ensure_future(auth_cache.cached_memberships("user_1", fetch))
        await asyncio.sleep(0)

        await auth_cache.reset()

        assert auth_cache._membership_inflight == {}  # noqa: SLF001
        with pytest.raises(asyncio.CancelledError):
            await waiter

    anyio.run(scenario)


def test_a_fully_abandoned_failing_flight_leaves_no_unretrieved_exception() -> None:
    """The done-callback must consume the exception of an orphaned task.

    "Task exception was never retrieved" is reported to the event loop's
    exception handler at GC time, not through the warnings module — so this
    installs a handler and forces collection. It must NOT await the failing
    task: awaiting would retrieve the exception itself and make the assertion
    vacuous.
    """
    import gc

    configure(30.0)
    handled: list[dict] = []

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: handled.append(context))
        gate = asyncio.Event()

        async def failing() -> VerifiedIdentity:
            await gate.wait()
            raise HTTPException(status_code=503, detail="unavailable")

        waiter = asyncio.ensure_future(auth_cache.cached_verify("token-a", failing))
        await asyncio.sleep(0)

        # Abandon the only waiter, then let the underlying shielded task fail
        # with nobody left to observe it.
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        await auth_cache.reset()
        del waiter
        gc.collect()
        await asyncio.sleep(0)

    anyio.run(scenario)

    assert [
        context
        for context in handled
        if "never retrieved" in str(context.get("message", ""))
    ] == []


def test_reset_guards_task_exception_on_a_cancelled_task() -> None:
    """Task.exception() raises CancelledError; the done-callback must guard it.

    An unguarded call turns cleanup into a new error path, which surfaces as
    an "Exception in callback" report to the loop exception handler.
    """
    configure(30.0)
    handled: list[dict] = []

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: handled.append(context))
        gate = asyncio.Event()

        async def fetch() -> VerifiedIdentity:
            await gate.wait()
            return VerifiedIdentity(user_id="user_123")

        waiter = asyncio.ensure_future(auth_cache.cached_verify("token-a", fetch))
        await asyncio.sleep(0)

        # reset() cancels the underlying task, firing the done-callback on a
        # cancelled task.
        await auth_cache.reset()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await asyncio.sleep(0)

    anyio.run(scenario)

    assert handled == []

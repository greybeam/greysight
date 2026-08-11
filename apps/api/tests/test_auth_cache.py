import asyncio
import base64
import hashlib
import json
import threading

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services import auth_cache
from app.services.auth_cache import VerifiedIdentity
from app.services.membership_directory import Organization

# Every async test in this module runs on anyio's pytest plugin, pinned to the
# asyncio backend by the ``anyio_backend`` fixture in conftest.py.
pytestmark = pytest.mark.anyio


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


async def test_verify_hit_within_ttl_makes_one_upstream_call() -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_verifier()
    assert (await auth_cache.cached_verify("token-a", fetch)).user_id == "user_123"
    clock.now = 29.0
    assert (await auth_cache.cached_verify("token-a", fetch)).user_id == "user_123"

    assert len(calls) == 1


async def test_verify_refetches_after_ttl_expiry() -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_verifier()
    await auth_cache.cached_verify("token-a", fetch)
    clock.now = 30.1
    await auth_cache.cached_verify("token-a", fetch)

    assert len(calls) == 2


async def test_distinct_tokens_do_not_cross_contaminate() -> None:
    configure(30.0)

    async def fetch_a() -> VerifiedIdentity:
        return VerifiedIdentity(user_id="user_a")

    async def fetch_b() -> VerifiedIdentity:
        return VerifiedIdentity(user_id="user_b")

    assert (await auth_cache.cached_verify("token-a", fetch_a)).user_id == "user_a"
    assert (await auth_cache.cached_verify("token-b", fetch_b)).user_id == "user_b"
    assert (await auth_cache.cached_verify("token-a", fetch_b)).user_id == "user_a"


async def test_failed_verification_is_never_cached_and_a_later_success_populates() -> (
    None
):
    configure(30.0)
    calls: list[int] = []

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

    assert len(calls) == 3


async def test_ttl_zero_disables_verification_storage_and_coalescing() -> None:
    """Disabled means passthrough: no storage AND no single-flight.

    The concurrent half is the load-bearing assertion — a sequential pair
    would also pass if TTL=0 merely skipped storage while still collapsing
    concurrent callers onto one shared fetch. The empty-registry assertions
    pin the short-circuit ahead of registration, not merely ahead of storage.
    """
    configure(0.0)
    calls: list[int] = []
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
    assert auth_cache._verify_inflight == {}  # noqa: SLF001
    assert auth_cache._membership_inflight == {}  # noqa: SLF001
    gate.set()
    await asyncio.gather(*waiters)

    # Sequential calls also each reach the fetch.
    await auth_cache.cached_verify("token-a", fetch)

    assert len(calls) == 5


async def test_reset_clears_entries_but_preserves_the_configured_ttl() -> None:
    clock, _ = configure(45.0)
    calls, fetch = make_verifier()
    await auth_cache.cached_verify("token-a", fetch)
    await auth_cache.reset()
    await auth_cache.cached_verify("token-a", fetch)
    clock.now = 44.0
    await auth_cache.cached_verify("token-a", fetch)

    # 2 = one before reset, one after; the third call is a hit because the
    # 45s TTL survived reset().
    assert len(calls) == 2


def test_both_caches_are_built_bounded_at_max_entries() -> None:
    """Both caches must be *bounded*, and bounded at the declared constant.

    Keys are attacker-influenceable (a sha256 of any presented bearer token,
    and any user id that reaches a lookup), so an unbounded cache is an
    unbounded-memory-growth path. Asserting only `max_entries == MAX_ENTRIES`
    would pass with MAX_ENTRIES raised to a nonsense value, so pin the
    constant too.
    """
    configure(30.0)

    assert auth_cache.MAX_ENTRIES == 1024
    verify_cache = auth_cache._verify_cache  # noqa: SLF001 — bound assertion
    membership_cache = auth_cache._membership_cache  # noqa: SLF001
    assert verify_cache is not None
    assert membership_cache is not None
    assert verify_cache._max_entries == auth_cache.MAX_ENTRIES  # noqa: SLF001
    assert membership_cache._max_entries == auth_cache.MAX_ENTRIES  # noqa: SLF001


async def test_token_is_never_used_as_a_cache_key() -> None:
    configure(30.0)
    _, fetch = make_verifier()
    await auth_cache.cached_verify("super-secret-token", fetch)

    keys = list(auth_cache._verify_cache._entries)  # noqa: SLF001 — key-shape check
    assert "super-secret-token" not in keys
    assert all(len(key) == 64 for key in keys)


def jwt_with_exp(exp: object) -> str:
    """A structurally valid JWT whose payload carries the given exp."""

    def segment(payload: dict[str, object]) -> str:
        raw = json.dumps(payload).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{segment({'alg': 'HS256'})}.{segment({'sub': 'user_123', 'exp': exp})}.sig"


async def test_token_exp_before_the_ttl_boundary_stops_being_served_at_exp() -> None:
    clock, wall = configure(30.0)
    calls, fetch = make_verifier()
    # exp is 10s of wall-clock away, well inside the 30s TTL.
    token = jwt_with_exp(wall.now + 10.0)
    await auth_cache.cached_verify(token, fetch)
    clock.now = 9.9
    await auth_cache.cached_verify(token, fetch)
    assert len(calls) == 1
    clock.now = 10.0
    await auth_cache.cached_verify(token, fetch)

    assert len(calls) == 2


async def test_a_wall_clock_jump_does_not_extend_acceptance() -> None:
    clock, wall = configure(30.0)
    calls, fetch = make_verifier()
    token = jwt_with_exp(wall.now + 10.0)
    await auth_cache.cached_verify(token, fetch)
    # Wall clock jumps backwards an hour mid-TTL; the deadline is
    # monotonic-only, so it cannot buy the entry extra life.
    wall.now -= 3600.0
    clock.now = 10.0
    await auth_cache.cached_verify(token, fetch)

    assert len(calls) == 2


async def test_an_already_expired_token_is_verified_but_never_cached() -> None:
    _, wall = configure(30.0)
    calls, fetch = make_verifier()
    token = jwt_with_exp(wall.now - 5.0)
    await auth_cache.cached_verify(token, fetch)
    await auth_cache.cached_verify(token, fetch)

    assert len(calls) == 2


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        "only.two",
        "aaa.!!!not-base64!!!.sig",
    ],
)
async def test_unparseable_tokens_fall_back_to_the_plain_ttl(token: str) -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_verifier()
    await auth_cache.cached_verify(token, fetch)
    clock.now = 29.0
    await auth_cache.cached_verify(token, fetch)

    assert len(calls) == 1


@pytest.mark.parametrize("exp", ["soon", None, True])
async def test_non_numeric_exp_falls_back_to_the_plain_ttl(exp: object) -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_verifier()
    token = jwt_with_exp(exp)
    await auth_cache.cached_verify(token, fetch)
    clock.now = 29.0
    await auth_cache.cached_verify(token, fetch)

    assert len(calls) == 1


@pytest.mark.parametrize(
    "exp",
    [
        pytest.param(int("9" * 4000), id="oversized-int"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(float("nan"), id="nan"),
    ],
)
async def test_an_unrepresentable_exp_falls_back_to_the_plain_ttl(exp: object) -> None:
    """An `exp` no float can hold is unreadable, not a 500.

    A 4000-digit integer raises OverflowError out of float(); the
    JSON-extension literals decode to non-finite floats that would poison
    every later comparison. Both are attacker-supplied — this runs before any
    signature check — and both must degrade to the plain TTL like every other
    unreadable exp.
    """
    clock, _ = configure(30.0)
    calls, fetch = make_verifier()
    token = jwt_with_exp(exp)

    assert auth_cache._unverified_exp(token) is None  # noqa: SLF001
    await auth_cache.cached_verify(token, fetch)
    clock.now = 29.0
    await auth_cache.cached_verify(token, fetch)

    assert len(calls) == 1


async def test_a_payload_that_is_not_a_json_object_falls_back_to_the_plain_ttl() -> (
    None
):
    """A payload segment may decode to any JSON value, not just an object."""
    clock, _ = configure(30.0)
    calls, fetch = make_verifier()

    def segment(payload: object) -> str:
        raw = json.dumps(payload).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    token = f"{segment({'alg': 'HS256'})}.{segment(['exp', 1])}.sig"

    assert auth_cache._unverified_exp(token) is None  # noqa: SLF001
    await auth_cache.cached_verify(token, fetch)
    clock.now = 29.0
    await auth_cache.cached_verify(token, fetch)

    assert len(calls) == 1


ORG_A = Organization(id="org-a", name="Acme")
ORG_B = Organization(id="org-b", name="Beta")


def make_lookup(*orgs: Organization) -> tuple[list[int], object]:
    calls: list[int] = []

    async def fetch() -> tuple[Organization, ...]:
        calls.append(1)
        return orgs

    return calls, fetch


async def test_membership_hit_within_ttl_makes_one_upstream_call() -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_lookup(ORG_A)
    assert await auth_cache.cached_memberships("user_1", fetch) == (ORG_A,)
    clock.now = 29.0
    assert await auth_cache.cached_memberships("user_1", fetch) == (ORG_A,)

    assert len(calls) == 1


async def test_membership_refetches_after_ttl_expiry() -> None:
    clock, _ = configure(30.0)
    calls, fetch = make_lookup(ORG_A)
    await auth_cache.cached_memberships("user_1", fetch)
    clock.now = 30.1
    await auth_cache.cached_memberships("user_1", fetch)

    assert len(calls) == 2


async def test_distinct_users_do_not_cross_contaminate() -> None:
    configure(30.0)
    _, fetch_a = make_lookup(ORG_A)
    _, fetch_b = make_lookup(ORG_B)
    assert await auth_cache.cached_memberships("user_1", fetch_a) == (ORG_A,)
    assert await auth_cache.cached_memberships("user_2", fetch_b) == (ORG_B,)
    assert await auth_cache.cached_memberships("user_1", fetch_b) == (ORG_A,)


async def test_failed_membership_lookup_is_never_cached() -> None:
    configure(30.0)
    calls: list[int] = []

    async def failing() -> tuple[Organization, ...]:
        calls.append(1)
        raise RuntimeError("upstream said no")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await auth_cache.cached_memberships("user_1", failing)

    assert len(calls) == 2


async def test_ttl_zero_disables_membership_storage_and_coalescing() -> None:
    """Concurrent callers must each reach the fetch, not share one."""
    configure(0.0)
    calls: list[int] = []
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

    assert len(calls) == 5


async def test_invalidate_user_forces_a_refetch_for_that_user_only() -> None:
    configure(30.0)
    calls_1, fetch_1 = make_lookup(ORG_A)
    calls_2, fetch_2 = make_lookup(ORG_B)
    await auth_cache.cached_memberships("user_1", fetch_1)
    await auth_cache.cached_memberships("user_2", fetch_2)
    auth_cache.invalidate_user("user_1")
    await auth_cache.cached_memberships("user_1", fetch_1)
    await auth_cache.cached_memberships("user_2", fetch_2)

    assert len(calls_1) == 2
    assert len(calls_2) == 1


async def test_invalidate_all_memberships_forces_a_refetch_for_everyone() -> None:
    configure(30.0)
    calls_1, fetch_1 = make_lookup(ORG_A)
    calls_2, fetch_2 = make_lookup(ORG_B)
    await auth_cache.cached_memberships("user_1", fetch_1)
    await auth_cache.cached_memberships("user_2", fetch_2)
    auth_cache.invalidate_all_memberships()
    await auth_cache.cached_memberships("user_1", fetch_1)
    await auth_cache.cached_memberships("user_2", fetch_2)

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
async def test_invalidation_mid_fetch_does_not_publish_the_stale_value(
    invalidate,
) -> None:
    """The classic race: fetch starts, invalidation lands, fetch returns stale."""
    configure(30.0)
    results = [(ORG_A,), (ORG_B,)]
    calls: list[int] = []

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


async def test_reset_zeroes_the_generation_counter() -> None:
    configure(30.0)
    auth_cache.invalidate_all_memberships()
    assert auth_cache._membership_generation > 0  # noqa: SLF001 — state assertion

    await auth_cache.reset()

    assert auth_cache._membership_generation == 0  # noqa: SLF001


async def test_concurrent_same_token_requests_collapse_to_one_upstream_call() -> None:
    configure(30.0)
    calls: list[int] = []
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

    assert len(calls) == 1


async def test_concurrent_same_user_membership_requests_collapse() -> None:
    configure(30.0)
    calls: list[int] = []
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

    assert len(calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        HTTPException(status_code=401, detail="Authentication required"),
        HTTPException(status_code=503, detail="Authentication service unavailable"),
    ],
    ids=["401", "503"],
)
async def test_every_waiter_on_a_failing_verify_receives_the_same_status(error) -> None:
    configure(30.0)
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


async def test_cancelling_the_leader_waiter_does_not_break_the_other_waiters() -> None:
    configure(30.0)
    calls: list[int] = []
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

    assert len(calls) == 1


async def test_a_fully_cancelled_flight_still_allows_a_successful_retry() -> None:
    configure(30.0)
    calls: list[int] = []
    # This test deliberately abandons a failing flight: its only waiter is
    # cancelled before the fetch raises. asyncio.shield retrieves the inner
    # task's exception in that case but also reports it to the loop exception
    # handler, and anyio's test runner re-raises anything that reaches the
    # default handler. Capture it instead, and assert it is exactly the
    # abandoned flight's 503 — nothing else may reach the handler.
    logged: list[BaseException | None] = []
    asyncio.get_running_loop().set_exception_handler(
        lambda _loop, context: logged.append(context.get("exception"))
    )
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

    assert (await auth_cache.cached_verify("token-a", succeeding)).user_id == "user_123"

    assert len(calls) == 2
    assert [type(exc) for exc in logged] == [HTTPException]
    assert [getattr(exc, "status_code", None) for exc in logged] == [503]


async def test_verify_and_membership_single_flight_are_isolated() -> None:
    """A colliding key in the other key space must never be served across."""
    configure(30.0)
    # Use the sha256 of a token as a *user id*, so a single shared registry
    # keyed by bare strings would hand one caller the other's value.
    token = "token-a"
    colliding_user_id = hashlib.sha256(token.encode("utf-8")).hexdigest()
    gate = asyncio.Event()

    async def verify_fetch() -> VerifiedIdentity:
        await gate.wait()
        return VerifiedIdentity(user_id="user_from_verify")

    async def membership_fetch() -> tuple[Organization, ...]:
        await gate.wait()
        return (ORG_B,)

    verify_waiter = asyncio.ensure_future(auth_cache.cached_verify(token, verify_fetch))
    membership_waiter = asyncio.ensure_future(
        auth_cache.cached_memberships(colliding_user_id, membership_fetch)
    )
    await asyncio.sleep(0)
    gate.set()

    identity = await verify_waiter
    organizations = await membership_waiter

    assert identity.user_id == "user_from_verify"
    assert organizations == (ORG_B,)


async def test_reset_cancels_and_drains_in_flight_tasks() -> None:
    configure(30.0)
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


async def test_reset_drains_a_membership_flight_and_leaves_no_pending_task() -> None:
    configure(30.0)
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


# NOT TESTED, DELIBERATELY: the `task.exception()` call in auth_cache._release
# is defensive-only and has no reachable behaviour to assert on this
# interpreter. _single_flight hands every caller an asyncio.shield of the task,
# and shield itself always retrieves the inner task's exception — either via
# _inner_done_callback (the normal path) or, once the outer future is
# cancelled, via the _log_on_exception callback that _outer_done_callback
# swaps in. Verified by stripping `task.exception()` from _release and
# re-running: every test in this file still passes, and an abandoned failing
# flight's task still shows its exception as retrieved. A test asserting that
# no "Task exception was never retrieved" report reaches the loop's exception
# handler would therefore be measuring shield, not _release. The `if not
# task.cancelled()` guard around that call IS load-bearing and is covered by
# test_reset_guards_task_exception_on_a_cancelled_task below.


async def test_reset_guards_task_exception_on_a_cancelled_task() -> None:
    """Task.exception() raises CancelledError; the done-callback must guard it.

    An unguarded call turns cleanup into a new error path, which surfaces as
    an "Exception in callback" report to the loop exception handler.
    """
    configure(30.0)
    handled: list[dict] = []
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

    assert handled == []


# --- The cache window is measured from fetch start, not fetch completion -----
#
# The upstream snapshots authorization state when the request *starts*, so an
# entry published at completion with a completion-relative deadline is served
# for TTL + fetch_duration. Both upstreams allow 10s timeouts, so a 30s TTL
# would otherwise buy a ~40s revocation window. These tests pin the deadline to
# fetch start on both paths.


def slow_verifier(
    clock: FakeClock,
    duration: float,
    *,
    wall: FakeWallClock | None = None,
) -> tuple[list[int], object]:
    """A verifier whose fetch advances the injected clocks while it runs.

    Both clocks advance together when a wall clock is supplied, because real
    elapsed time moves both — a fetch that burned monotonic seconds without
    burning wall seconds would make an `exp` deadline look further away than
    it is.
    """
    calls: list[int] = []

    async def fetch() -> VerifiedIdentity:
        calls.append(1)
        clock.now += duration
        if wall is not None:
            wall.now += duration
        return VerifiedIdentity(user_id="user_123")

    return calls, fetch


def slow_lookup(clock: FakeClock, duration: float) -> tuple[list[int], object]:
    calls: list[int] = []

    async def fetch() -> tuple[Organization, ...]:
        calls.append(1)
        clock.now += duration
        return (ORG_A,)

    return calls, fetch


async def test_a_slow_verify_is_not_served_past_the_fetch_start_deadline() -> None:
    clock, _ = configure(30.0)
    calls, fetch = slow_verifier(clock, 10.0)
    # Fetch starts at t=0 and returns at t=10, so the entry must stop
    # being served at t=30 — not at t=40.
    await auth_cache.cached_verify("token-a", fetch)
    clock.now = 29.9
    await auth_cache.cached_verify("token-a", fetch)
    assert len(calls) == 1, "an entry inside its window was refetched"
    clock.now = 30.0
    await auth_cache.cached_verify("token-a", fetch)

    assert len(calls) == 2


async def test_a_slow_membership_fetch_is_not_served_past_the_fetch_start_deadline() -> (
    None
):
    clock, _ = configure(30.0)
    calls, fetch = slow_lookup(clock, 10.0)
    await auth_cache.cached_memberships("user_1", fetch)
    clock.now = 29.9
    assert await auth_cache.cached_memberships("user_1", fetch) == (ORG_A,)
    assert len(calls) == 1, "an entry inside its window was refetched"
    clock.now = 30.0
    await auth_cache.cached_memberships("user_1", fetch)

    assert len(calls) == 2


async def test_a_verify_slower_than_the_whole_ttl_is_never_cached() -> None:
    clock, _ = configure(30.0)
    calls, fetch = slow_verifier(clock, 31.0)
    await auth_cache.cached_verify("token-a", fetch)
    # The deadline (t=30) already passed before the fetch returned, so
    # nothing may be published at all.
    assert auth_cache._verify_cache._entries == {}  # noqa: SLF001
    await auth_cache.cached_verify("token-a", fetch)

    assert len(calls) == 2


async def test_a_membership_fetch_slower_than_the_whole_ttl_is_never_cached() -> None:
    clock, _ = configure(30.0)
    calls, fetch = slow_lookup(clock, 31.0)
    await auth_cache.cached_memberships("user_1", fetch)
    assert auth_cache._membership_cache._entries == {}  # noqa: SLF001
    await auth_cache.cached_memberships("user_1", fetch)

    assert len(calls) == 2


async def test_token_exp_still_shortens_a_fetch_start_deadline() -> None:
    """`exp` may only shorten the window the fetch-start deadline sets."""
    clock, wall = configure(30.0)
    calls, fetch = slow_verifier(clock, 10.0, wall=wall)
    # exp lands 5s after the fetch returns (t=15), well inside t=30.
    token = jwt_with_exp(wall.now + 15.0)
    await auth_cache.cached_verify(token, fetch)
    clock.now = 14.9
    await auth_cache.cached_verify(token, fetch)
    assert len(calls) == 1
    clock.now = 15.0
    await auth_cache.cached_verify(token, fetch)

    assert len(calls) == 2


async def test_a_far_future_token_exp_cannot_lengthen_the_fetch_start_deadline() -> (
    None
):
    clock, wall = configure(30.0)
    calls, fetch = slow_verifier(clock, 10.0, wall=wall)
    token = jwt_with_exp(wall.now + 86_400.0)
    await auth_cache.cached_verify(token, fetch)
    clock.now = 30.0
    await auth_cache.cached_verify(token, fetch)

    assert len(calls) == 2


class HandoffLock:
    """``auth_cache._lock`` with one scripted release-point handoff.

    The first release *after* the membership fetch has returned hands control
    to the invalidator thread and blocks until it is done. Under a correct
    atomic compare-and-publish that release is the end of one critical
    section, so the invalidation necessarily lands after the publish and
    removes it. Split the section and the same release falls between the
    compare and the publish, so the invalidation is silently overwritten.
    """

    def __init__(
        self,
        fetch_done: threading.Event,
        publishing: threading.Event,
        invalidated: threading.Event,
        handoffs: list[bool],
    ) -> None:
        self._lock = threading.Lock()
        self._fetch_done = fetch_done
        self._publishing = publishing
        self._invalidated = invalidated
        self._handoffs = handoffs
        self._armed = True

    def acquire(self, *args, **kwargs):
        return self._lock.acquire(*args, **kwargs)

    def release(self) -> None:
        handing_off = self._armed and self._fetch_done.is_set()
        if handing_off:
            # Disarm before releasing: the invalidator takes this same lock.
            self._armed = False
        self._lock.release()
        if handing_off:
            self._publishing.set()
            self._handoffs.append(self._invalidated.wait(timeout=10.0))

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc_info) -> bool:
        self.release()
        return False


async def test_an_invalidation_between_the_compare_and_the_publish_is_not_lost() -> (
    None
):
    """A stale value must not be published around an interleaved invalidation.

    The narrow race: the fetch completes, the generation compares equal, and
    only *then* does invalidate_user land. If the compare and the publish are
    not one critical section, the delete happens first and the stale value is
    written after it, then served for a full TTL.

    Driven by a real thread and events, never by sleeps. The invalidator is
    started from inside the fetch, so the generation snapshot has already been
    taken, and it is parked until the scripted handoff releases it.
    """
    configure(30.0)
    calls: list[int] = []
    fetch_done = threading.Event()
    publishing = threading.Event()
    invalidated = threading.Event()
    handoffs: list[bool] = []
    reached_handoff: list[bool] = []

    def invalidator() -> None:
        reached_handoff.append(publishing.wait(timeout=10.0))
        auth_cache.invalidate_user("user_1")
        invalidated.set()

    thread = threading.Thread(target=invalidator, name="invalidator")

    async def fetch() -> tuple[Organization, ...]:
        calls.append(1)
        if len(calls) == 1:
            thread.start()
            fetch_done.set()
        return (ORG_A,)

    real_lock = auth_cache._lock  # noqa: SLF001 — the seam under test
    auth_cache._lock = HandoffLock(  # noqa: SLF001
        fetch_done, publishing, invalidated, handoffs
    )

    try:
        assert await auth_cache.cached_memberships("user_1", fetch) == (ORG_A,)

        thread.join(timeout=10.0)
        assert not thread.is_alive(), "the invalidator thread never finished"
        assert reached_handoff == [True], "the handoff point was never reached"
        assert handoffs == [True], "the invalidation never completed"

        # The invalidated user must not be served a value published around
        # that invalidation: the next read has to hit the upstream again.
        assert await auth_cache.cached_memberships("user_1", fetch) == (ORG_A,)
        assert len(calls) == 2, "a value invalidated mid-publish was served"
    finally:
        auth_cache._lock = real_lock  # noqa: SLF001

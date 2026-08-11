"""In-process caching for Supabase auth verification and org memberships.

Both hops are cached under a short TTL that is also the maximum revocation
delay: nothing here may extend acceptance past that TTL. Successes only — a
401 or 503 never populates or extends an entry, and there is no
stale-on-error fallback.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.config import Settings
from app.services.membership_directory import Organization
from app.services.ttl_cache import TtlCache

MAX_ENTRIES = 1024


@dataclass(frozen=True)
class VerifiedIdentity:
    """A validated identity, not a raw claims mapping.

    ``sub`` validation happens inside the cached fetch boundary, so a 200
    response with a malformed body raises 401 and never reaches the cache.
    ``expires_at_monotonic`` is named for its clock so it cannot be mistaken
    for an epoch value.
    """

    user_id: str
    expires_at_monotonic: float | None = None


_ttl_seconds: float = 30.0
_verify_cache: TtlCache | None = None
_membership_cache: TtlCache | None = None
_monotonic_clock: Callable[[], float] = time.monotonic
_wall_clock: Callable[[], float] = time.time

# One process-wide counter guarded by an auth_cache-owned outer lock. TtlCache
# keeps its own internal lock for its own atomicity, but every membership-cache
# mutation and every generation read/bump made here happens inside this lock —
# that is what makes compare-and-publish atomic with respect to invalidation.
# A per-user map was rejected: it grows without bound, and mutations are rare.
_lock = threading.Lock()
_membership_generation = 0

# Two registries, not one: a single map keyed by bare strings would let the
# verify key space (sha256 hex) and the membership key space (user ids)
# collide. Both are touched only from the event loop — check-and-create is
# synchronous, so it is atomic under asyncio and needs no lock. Sync
# threadpool invalidators must never touch them.
_verify_inflight: dict[str, "asyncio.Task[VerifiedIdentity]"] = {}
_membership_inflight: dict[str, "asyncio.Task[tuple[Organization, ...]]"] = {}


def configure_auth_cache(
    settings: Settings,
    *,
    monotonic_clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
) -> None:
    """Build the caches from settings. The only place TTL is (re)configured.

    The monotonic clock is passed through to the TtlCache instances so the
    cache and the token-expiry deadline share one clock.
    """
    global _ttl_seconds, _verify_cache, _membership_cache
    global _monotonic_clock, _wall_clock
    _ttl_seconds = float(settings.auth_cache_ttl_seconds)
    _monotonic_clock = monotonic_clock
    _wall_clock = wall_clock
    _verify_cache = TtlCache(
        ttl_seconds=_ttl_seconds, max_entries=MAX_ENTRIES, clock=monotonic_clock
    )
    _membership_cache = TtlCache(
        ttl_seconds=_ttl_seconds, max_entries=MAX_ENTRIES, clock=monotonic_clock
    )


async def cached_verify(
    token: str,
    fetch: Callable[[], Awaitable[VerifiedIdentity]],
) -> VerifiedIdentity:
    if _ttl_seconds <= 0 or _verify_cache is None:
        # Disabled means passthrough: short-circuit before hashing or locking.
        return await fetch()

    key = _token_key(token)
    hit = _verify_cache.get(key)
    if hit is not None and not _is_expired(hit):
        return hit

    return await _single_flight(
        _verify_inflight, key, lambda: _verify_and_store(key, token, fetch)
    )


async def _verify_and_store(
    key: str,
    token: str,
    fetch: Callable[[], Awaitable[VerifiedIdentity]],
) -> VerifiedIdentity:
    # Read the clock before awaiting: the upstream snapshots authorization
    # state when the request starts, so a completion-relative deadline would
    # make the real revocation window TTL + fetch duration.
    started = _monotonic_clock()
    identity = await fetch()

    deadline = started + _ttl_seconds
    token_deadline = _expiry_deadline(token)
    if token_deadline is not None:
        # exp may only shorten the window, never lengthen it.
        deadline = min(deadline, token_deadline)

    identity = VerifiedIdentity(user_id=identity.user_id, expires_at_monotonic=deadline)
    if _verify_cache is not None and not _is_expired(identity):
        _verify_cache.set(key, identity, expires_at=deadline)
    return identity


def _single_flight(
    registry: dict[str, "asyncio.Task"],
    key: str,
    factory: Callable[[], Awaitable],
) -> Awaitable:
    """Collapse concurrent work on one key onto one task.

    Callers await a shield so a cancelled waiter neither kills the leader nor
    strands the other waiters, and CancelledError cannot leak through an
    `except Exception` and leave waiters blocked. Exceptions propagate
    identically to every waiter, preserving 401-vs-503 per waiter.
    """
    task = registry.get(key)
    if task is None:
        task = asyncio.ensure_future(factory())
        registry[key] = task
        task.add_done_callback(lambda finished: _release(registry, key, finished))
    return asyncio.shield(task)


def _release(
    registry: dict[str, "asyncio.Task"],
    key: str,
    task: "asyncio.Task",
) -> None:
    if registry.get(key) is task:
        del registry[key]
    # Consume the exception so a task whose waiters were all cancelled does
    # not surface as "Task exception was never retrieved" at GC time. The
    # guard is required: Task.exception() *raises* CancelledError on a
    # cancelled task, which would turn cleanup into a new error path.
    if not task.cancelled():
        task.exception()


def _is_expired(identity: VerifiedIdentity) -> bool:
    deadline = identity.expires_at_monotonic
    return deadline is not None and _monotonic_clock() >= deadline


def _expiry_deadline(token: str) -> float | None:
    """Convert the token's wall-clock ``exp`` into a monotonic deadline.

    JWT ``exp`` is epoch seconds; TtlCache runs on time.monotonic. Comparing
    them directly is a bug, so convert exactly once, here, and compare only
    monotonic values afterwards — a wall-clock adjustment mid-TTL then cannot
    extend acceptance.
    """
    exp_epoch = _unverified_exp(token)
    if exp_epoch is None:
        return None
    return _monotonic_clock() + max(0.0, exp_epoch - _wall_clock())


def _unverified_exp(token: str) -> float | None:
    """Read ``exp`` from the JWT payload without verifying the signature.

    Safe because the auth server has already verified this token and the
    value is used only to *shorten* the cache window, never to extend it.
    Anything unreadable falls back to the plain TTL.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    segment = parts[1]
    padding = "=" * (-len(segment) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(segment + padding))
    except (ValueError, binascii.Error):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    try:
        exp_seconds = float(exp)
    except OverflowError:
        # An arbitrarily large integer literal is legal JSON; float() rejects
        # it. This runs on an unverified payload, so it must not raise.
        return None
    if not math.isfinite(exp_seconds):
        # json.loads accepts Infinity/NaN. Either would poison every later
        # deadline comparison, so treat them as unreadable too.
        return None
    return exp_seconds


async def cached_memberships(
    user_id: str,
    fetch: Callable[[], Awaitable[tuple[Organization, ...]]],
) -> tuple[Organization, ...]:
    if _ttl_seconds <= 0 or _membership_cache is None:
        return await fetch()

    hit = _membership_cache.get(user_id)
    if hit is not None:
        return hit
    return await _single_flight(
        _membership_inflight, user_id, lambda: _fetch_memberships(user_id, fetch)
    )


async def _fetch_memberships(
    user_id: str,
    fetch: Callable[[], Awaitable[tuple[Organization, ...]]],
) -> tuple[Organization, ...]:
    with _lock:
        generation = _membership_generation

    # Same rule as the verify path: the window starts when the fetch starts.
    started = _monotonic_clock()
    organizations = tuple(await fetch())
    expires_at = started + _ttl_seconds

    with _lock:
        # One atomic compare-and-publish. Re-checking the generation and then
        # calling set() under separate lock acquisitions would reintroduce
        # exactly the race this exists to close.
        if _membership_generation == generation and _membership_cache is not None:
            # A fetch slower than the whole TTL leaves nothing to publish; the
            # value is still returned to this caller, just uncached.
            if _monotonic_clock() < expires_at:
                _membership_cache.set(user_id, organizations, expires_at=expires_at)
            return organizations

    # Invalidated mid-flight: never publish. One bounded refetch (not a loop,
    # which could livelock under repeated invalidation), returned uncached.
    return tuple(await fetch())


def invalidate_user(user_id: str) -> None:
    """Drop one user's memberships. Safe to call from a sync threadpool route.

    Touches only lock-guarded TtlCache state and the generation counter —
    never the in-flight registries, which belong to the event loop.
    """
    global _membership_generation
    with _lock:
        if _membership_cache is not None:
            _membership_cache.delete(user_id)
        _membership_generation += 1


def invalidate_all_memberships() -> None:
    """Flush every user's memberships. Safe from a sync threadpool route.

    A cached Organization carries account_locator and connection_status, so a
    disconnect goes stale for every member of the org, not just the caller.
    There is no org->user reverse index and disconnects are infrequent, so a
    global flush beats maintaining one.
    """
    global _membership_generation
    with _lock:
        if _membership_cache is not None:
            _membership_cache.clear()
        _membership_generation += 1


async def reset() -> None:
    """Clear all cached and in-flight state; preserve the configured TTL.

    Cancels in-flight tasks and *awaits* them before returning. The lifespan
    calls this before closing the pooled HTTP clients: a task still mid-
    request against a closing client produces spurious shutdown errors, and a
    fire-and-forget cancel() without a drain does not prevent that. Draining
    also consumes each task's exception, so a cancelled-waiter task never
    resurfaces as an unretrieved-exception warning.

    Reconfiguration is configure_auth_cache's job alone, so a test that
    deliberately set a TTL keeps it.
    """
    global _membership_generation

    tasks = [*_verify_inflight.values(), *_membership_inflight.values()]
    _verify_inflight.clear()
    _membership_inflight.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    with _lock:
        if _verify_cache is not None:
            _verify_cache.clear()
        if _membership_cache is not None:
            _membership_cache.clear()
        _membership_generation = 0


def _token_key(token: str) -> str:
    """Hash the bearer token; the raw token is never a cache key."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

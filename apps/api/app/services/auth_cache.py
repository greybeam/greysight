"""In-process caching for Supabase auth verification and org memberships.

Both hops are cached under a short TTL that is also the maximum revocation
delay: nothing here may extend acceptance past that TTL. Successes only — a
401 or 503 never populates or extends an entry, and there is no
stale-on-error fallback.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.config import Settings
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
    global _ttl_seconds, _verify_cache, _monotonic_clock, _wall_clock
    _ttl_seconds = float(settings.auth_cache_ttl_seconds)
    _monotonic_clock = monotonic_clock
    _wall_clock = wall_clock
    _verify_cache = TtlCache(
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
    if hit is not None:
        return hit

    identity = await fetch()
    _verify_cache.set(key, identity)
    return identity


async def reset() -> None:
    """Drop all cached state. Preserves the configured TTL and clocks.

    Async because later tasks add in-flight asyncio tasks that must be
    cancelled and drained here. Reconfiguration is configure_auth_cache's
    job alone, so a test that deliberately set a TTL keeps it.
    """
    if _verify_cache is not None:
        _verify_cache.clear()


def _token_key(token: str) -> str:
    """Hash the bearer token; the raw token is never a cache key."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

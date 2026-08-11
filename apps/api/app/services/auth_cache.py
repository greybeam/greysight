"""In-process caching for Supabase auth verification and org memberships.

Both hops are cached under a short TTL that is also the maximum revocation
delay: nothing here may extend acceptance past that TTL. Successes only — a
401 or 503 never populates or extends an entry, and there is no
stale-on-error fallback.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
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
    if hit is not None and not _is_expired(hit):
        return hit

    identity = await fetch()
    identity = VerifiedIdentity(
        user_id=identity.user_id,
        expires_at_monotonic=_expiry_deadline(token),
    )
    if not _is_expired(identity):
        _verify_cache.set(key, identity)
    return identity


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
    return float(exp)


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

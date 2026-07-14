"""Per-tenant async polling loop plus the dynamic tenant supervisor.

Three layers, cleanly separated so each is independently testable:

* ``run_tenant_once`` — a single guarded tick. Serialized per-org by an
  ``asyncio.Lock`` (a slow tenant never overlaps its own polls), the blocking
  ``show_warehouses`` is pushed to a thread pool and bounded by
  ``asyncio.wait_for(poll_timeout_seconds)``. On ANY failure the warm session
  is force-closed and the error re-raised so the caller backs off.

  Escape has two independent guarantees: ``wait_for`` frees the *event loop*
  (this module), while the connector socket timeout (Task 11, set strictly
  ``< poll_timeout_seconds``) frees the *pool thread* — a blocked C-level
  ``recv`` cannot be cancelled from Python, it can only time out on its own.

* ``tenant_loop`` — polls forever until its ``stop`` event is set, sleeping
  ``poll_interval_seconds`` on success (backoff reset) and a jittered
  exponential backoff on failure.

* ``supervisor`` — re-enumerates owned tenants every ``tenant_refresh_seconds``,
  starting loops for newcomers and stop-signalling + draining loops whose
  tenant vanished (kill-switched / drained), releasing each removed tenant's
  warm Snowflake session so it does not leak.
"""

from __future__ import annotations

import asyncio
import logging
import random
from concurrent.futures import Executor
from datetime import datetime, timezone
from typing import Callable, NamedTuple

from greysight_connect.org_connection_resolver import (
    OrgConnectionNotConfiguredError,
    OrgConnectionUnavailableError,
)

from auto_savings.config import WorkerConfig
from auto_savings.engine import run_cycle
from auto_savings.sharding import owns_tenant
from auto_savings.snowflake_session import TenantSession, next_backoff
from auto_savings.store import Store

logger = logging.getLogger(__name__)

NowFn = Callable[[], datetime]
JitterFn = Callable[[], float]
SleepFn = Callable[[float], "asyncio.Future[None]"]
# A factory resolves an org's connection ONCE and returns both the warm session
# and the fingerprint of that same resolved connection, so the two can never be
# derived from separate reads that disagree after a rotation (finding #2).
SessionFactory = Callable[[str], "tuple[TenantSession, str | None]"]
FingerprintFn = Callable[[str], str]


class _RunningLoop(NamedTuple):
    """A live per-tenant loop plus the state needed to supervise it."""

    task: "asyncio.Task[None]"
    stop_event: asyncio.Event
    session: TenantSession
    # Fingerprint of the Snowflake connection the session was built against, or
    # None when no fingerprint function is wired. A change means the org rotated
    # its account/credentials and the warm session must be recycled.
    fingerprint: str | None

# One asyncio.Lock per org so a slow tenant never overlaps its own polls, even
# if two ticks are somehow dispatched concurrently for the same tenant.
_locks: dict[str, asyncio.Lock] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lock_for(org_id: str) -> asyncio.Lock:
    lock = _locks.get(org_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[org_id] = lock
    return lock


async def run_tenant_once(
    org_id: str,
    *,
    session: TenantSession,
    store: Store,
    config: WorkerConfig,
    executor: Executor,
    now_fn: NowFn,
) -> bool:
    """One guarded poll → cycle tick for a single tenant.

    On success runs the engine cycle and returns whether any restore-intent
    remains outstanding (so the caller can fast-poll). If a failed tick started,
    the warm session is force-closed; a still-queued tick is cancelled. The
    original error is re-raised so the caller backs off.
    """
    lock = _lock_for(org_id)
    async with lock:
        loop = asyncio.get_running_loop()

        def _tick() -> bool:
            # The ENTIRE blocking tick runs on a pool thread: the Snowflake
            # SHOW WAREHOUSES *and* run_cycle (which does Supabase httpx reads
            # and Snowflake ALTERs). Running run_cycle on the event loop would
            # stall every other tenant and the supervisor.
            rows = session.show_warehouses()
            return run_cycle(
                org_id,
                rows=rows,
                store=store,
                config=config,
                now=now_fn(),
                apply_alter=session.alter_auto_suspend,
            )

        # Retain the underlying concurrent future so a still-queued tick can be
        # cancelled before the executor ever starts it. The asyncio wrapper alone
        # cannot tell us whether cancellation reached the executor work item.
        concurrent_future = executor.submit(_tick)
        future = asyncio.wrap_future(concurrent_future, loop=loop)
        try:
            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=config.poll_timeout_seconds,
            )
        except BaseException:
            if concurrent_future.cancel():
                # The tick never touched the session, so there is nothing to close
                # or drain, and it cannot run when pool capacity appears later.
                raise
            # Cleanup a possibly-wedged connection; closing the socket makes the
            # blocked recv on the pool thread raise promptly.
            session.close_hard()
            # GUARANTEED drain: wait for the abandoned _tick thread to ACTUALLY
            # terminate before returning, so the NEXT tick can never overlap it
            # on the same session/connection (concurrent Supabase/Snowflake
            # mutation). This await is unbounded on purpose but cannot hang: every
            # blocking op the thread can run is bounded — Snowflake by
            # socket_timeout_seconds (the close above frees a wedged recv) and
            # every Supabase store call by store_timeout_seconds — so the thread
            # is guaranteed to finish promptly. asyncio.wait never re-raises the
            # future's own exception, so we surface the ORIGINAL error below.
            await asyncio.wait({future})
            if not future.cancelled():
                # Mark a released worker failure as retrieved. The timeout/error
                # that brought us here remains the authoritative exception.
                future.exception()
            raise


async def tenant_loop(
    org_id: str,
    *,
    session: TenantSession,
    store: Store,
    config: WorkerConfig,
    executor: Executor,
    sleep: SleepFn = asyncio.sleep,
    stop: asyncio.Event | None = None,
    now_fn: NowFn = _utcnow,
    jitter: JitterFn = random.random,
) -> None:
    """Poll a single tenant until ``stop`` is set.

    Success → sleep ``poll_interval_seconds`` and reset the backoff attempt.
    Failure → sleep ``next_backoff(attempt, …)`` and increment the attempt.
    """
    if stop is None:
        stop = asyncio.Event()
    attempt = 0
    while not stop.is_set():
        try:
            has_intents = await run_tenant_once(
                org_id,
                session=session,
                store=store,
                config=config,
                executor=executor,
                now_fn=now_fn,
            )
        except Exception:
            delay = next_backoff(attempt, jitter=jitter)
            attempt += 1
            await sleep(delay)
        else:
            attempt = 0
            # Fast-poll while an intent is outstanding to shrink the
            # AUTO_SUSPEND=1-live window. The ±15% jitter is applied to BOTH the
            # fast intent-poll cadence AND the normal steady-state cadence
            # (finding #17) — this is deliberate, not an oversight: without it,
            # every tenant on this replica would settle into the exact same
            # poll_interval_seconds phase and hammer Snowflake/Supabase in
            # lockstep. Jittering steady-state too spreads the fleet's poll
            # timing evenly instead of phase-locking on a shared cadence.
            base = (
                config.intent_poll_interval_seconds
                if has_intents
                else config.poll_interval_seconds
            )
            delay = base * (0.85 + 0.30 * jitter())
            await sleep(delay)


async def supervisor(
    *,
    store: Store,
    config: WorkerConfig,
    executor: Executor,
    session_factory: SessionFactory,
    fingerprint_fn: FingerprintFn | None = None,
    sleep: SleepFn = asyncio.sleep,
    stop: asyncio.Event | None = None,
) -> None:
    """Dynamically start/stop/recycle per-tenant loops as ownership changes.

    Every ``tenant_refresh_seconds`` re-enumerate ``worker_tenants()`` filtered
    by ``owns_tenant``. Then, on each refresh:

    * start a loop for any newly-owned tenant;
    * stop-signal, drain, and release the warm session of any tenant that
      vanished so removed tenants do not leak Snowflake sessions;
    * restart any owned tenant whose loop task has crashed/exited (so a tenant
      never silently goes dark);
    * when ``fingerprint_fn`` is wired, re-resolve every owned tenant's
      connection and recycle its warm session if the account/credentials
      rotated, or drop it if the connection disappeared.
    """
    running: dict[str, _RunningLoop] = {}
    loop = asyncio.get_running_loop()
    try:
        while stop is None or not stop.is_set():
            try:
                owned = {
                    tenant
                    for tenant in store.worker_tenants()
                    if owns_tenant(
                        tenant,
                        num_replicas=config.num_replicas,
                        replica_index=config.replica_index,
                    )
                }
            except Exception:
                # A transient store failure must not kill the supervisor; retry
                # on the next refresh with the current set of loops intact.
                await sleep(config.tenant_refresh_seconds)
                continue

            for org_id in owned - running.keys():
                started = _start_loop(
                    org_id,
                    session_factory=session_factory,
                    store=store,
                    config=config,
                    executor=executor,
                    sleep=sleep,
                )
                if started is not None:
                    running[org_id] = started

            for org_id in [org for org in running if org not in owned]:
                await _drain_loop(running.pop(org_id))

            # Revalidate still-owned loops: restart crashed tasks (#12b) and
            # recycle warm sessions whose connection rotated/vanished (#3).
            for org_id in list(running.keys()):
                entry = running[org_id]

                # A loop that raised out of its while-loop is done but still
                # recorded as running → the tenant is dark. Drain and recreate.
                if entry.task.done():
                    await _drain_loop(entry)
                    running.pop(org_id)
                    restarted = _start_loop(
                        org_id,
                        session_factory=session_factory,
                        store=store,
                        config=config,
                        executor=executor,
                        sleep=sleep,
                    )
                    if restarted is not None:
                        running[org_id] = restarted
                    continue

                if fingerprint_fn is None:
                    continue
                try:
                    # In production fingerprint_fn does a SYNC Supabase HTTP call;
                    # run it on the pool thread so the supervisor loop never blocks
                    # on network I/O (which would delay sleeps and weaken the
                    # watchdog across many tenants — finding #IMPORTANT-3).
                    current = await loop.run_in_executor(executor, fingerprint_fn, org_id)
                except OrgConnectionUnavailableError:
                    # A TRANSIENT lookup failure (network/timeout/5xx): we cannot
                    # tell whether the connection is gone. KEEP the warm session
                    # and retry next refresh rather than dropping a healthy tenant.
                    logger.warning(
                        "Transient connection revalidation failure for %s; "
                        "keeping session",
                        org_id,
                    )
                    continue
                except OrgConnectionNotConfiguredError:
                    # Connection DEFINITIVELY gone (disconnected/invalidated):
                    # treat like a vanished tenant — stop + close. It restarts on
                    # a later refresh if the connection reappears.
                    await _drain_loop(entry)
                    running.pop(org_id)
                    continue
                except Exception:
                    # Any other unexpected error must not kill the warm session
                    # or the refresh; keep the current loop and retry next time.
                    logger.exception(
                        "Failed to revalidate connection for %s; keeping session",
                        org_id,
                    )
                    continue
                if current != entry.fingerprint:
                    # Account/credentials rotated: the warm session targets the
                    # OLD account. Stop + close it and start a fresh one.
                    await _drain_loop(entry)
                    running.pop(org_id)
                    restarted = _start_loop(
                        org_id,
                        session_factory=session_factory,
                        store=store,
                        config=config,
                        executor=executor,
                        sleep=sleep,
                    )
                    if restarted is not None:
                        running[org_id] = restarted

            await sleep(config.tenant_refresh_seconds)
    finally:
        # Shutdown: stop, drain, and release every remaining tenant loop.
        for org_id in list(running.keys()):
            await _drain_loop(running.pop(org_id))


def _start_loop(
    org_id: str,
    *,
    session_factory: SessionFactory,
    store: Store,
    config: WorkerConfig,
    executor: Executor,
    sleep: SleepFn,
) -> _RunningLoop | None:
    """Build a warm session (and its fingerprint) and spawn its tenant loop.

    The factory resolves the org's connection ONCE and returns both the session
    and the fingerprint of that same connection, so they can never disagree
    (finding #2). Building can fail (e.g. no Snowflake config yet →
    OrgConnectionNotConfiguredError). Isolate that failure to THIS tenant: log
    and return None so every other tenant keeps running. It retries next refresh.
    """
    try:
        session, fingerprint = session_factory(org_id)
    except Exception:
        logger.exception(
            "Failed to start tenant loop for %s; skipping this refresh", org_id
        )
        return None

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        tenant_loop(
            org_id,
            session=session,
            store=store,
            config=config,
            executor=executor,
            sleep=sleep,
            stop=stop_event,
        )
    )
    return _RunningLoop(task, stop_event, session, fingerprint)


async def _drain_loop(entry: _RunningLoop) -> None:
    """Stop a running tenant loop and release its warm session."""
    entry.stop_event.set()
    try:
        await entry.task
    except asyncio.CancelledError:
        raise
    except Exception:
        # The loop already handles its own errors; a failed task on teardown is
        # not fatal to the supervisor's shutdown.
        pass
    finally:
        entry.session.close_hard()

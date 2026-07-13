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
from typing import Callable

from auto_savings.config import WorkerConfig
from auto_savings.engine import run_cycle
from auto_savings.sharding import owns_tenant
from auto_savings.snowflake_session import TenantSession, next_backoff
from auto_savings.store import Store

logger = logging.getLogger(__name__)

NowFn = Callable[[], datetime]
JitterFn = Callable[[], float]
SleepFn = Callable[[float], "asyncio.Future[None]"]
SessionFactory = Callable[[str], TenantSession]

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
    remains outstanding (so the caller can fast-poll). On ANY failure (including
    a timeout) the warm session is force-closed and the error re-raised so the
    caller backs off.
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

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(executor, _tick),
                timeout=config.poll_timeout_seconds,
            )
        except BaseException:
            # Cleanup a possibly-wedged connection; the socket timeout frees the
            # pool thread on its own. Re-raise so the loop backs off / the
            # supervisor can decide the tenant's fate.
            session.close_hard()
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
            # AUTO_SUSPEND=1-live window; ±15% jitter avoids phase-locking the
            # executor across tenants.
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
    sleep: SleepFn = asyncio.sleep,
    stop: asyncio.Event | None = None,
) -> None:
    """Dynamically start/stop per-tenant loops as ownership changes.

    Every ``tenant_refresh_seconds`` re-enumerate ``worker_tenants()`` filtered
    by ``owns_tenant``. Start a loop for any newly-owned tenant; stop-signal,
    drain, and release the warm session of any tenant that vanished so removed
    tenants do not leak Snowflake sessions.
    """
    # org_id -> (loop task, stop event, warm session)
    running: dict[str, tuple[asyncio.Task[None], asyncio.Event, TenantSession]] = {}
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
                # Building a tenant's session can fail (e.g. no Snowflake config
                # yet → OrgConnectionNotConfiguredError). Isolate that failure to
                # THIS tenant: log and skip so every other tenant keeps running
                # and the drain below is never reached. It retries next refresh.
                try:
                    session = session_factory(org_id)
                except Exception:
                    logger.exception(
                        "Failed to start tenant loop for %s; skipping this refresh",
                        org_id,
                    )
                    continue
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
                running[org_id] = (task, stop_event, session)

            for org_id in [org for org in running if org not in owned]:
                await _drain_loop(*running.pop(org_id))

            await sleep(config.tenant_refresh_seconds)
    finally:
        # Shutdown: stop, drain, and release every remaining tenant loop.
        for org_id in list(running.keys()):
            await _drain_loop(*running.pop(org_id))


async def _drain_loop(
    task: "asyncio.Task[None]",
    stop_event: asyncio.Event,
    session: TenantSession,
) -> None:
    """Stop a running tenant loop and release its warm session."""
    stop_event.set()
    try:
        await task
    except asyncio.CancelledError:
        raise
    except Exception:
        # The loop already handles its own errors; a failed task on teardown is
        # not fatal to the supervisor's shutdown.
        pass
    finally:
        session.close_hard()

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import gc
import threading

import pytest

import auto_savings.tenant_loop as tenant_loop_mod
from auto_savings.config import WorkerConfig
from auto_savings.engine import CycleResult
from auto_savings.snowflake_session import SuspendOutcome, SuspendResult
from auto_savings.store import EnrollmentRow, InMemoryStore, SettingsRow
from auto_savings.tenant_loop import run_tenant_once, supervisor, tenant_loop

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = WorkerConfig(supabase_url="u", supabase_service_role_key="k")


class FakeSession:
    def __init__(self, rows=None, raise_exc=None):
        self._rows = rows or []
        self._raise = raise_exc
        self.closed = False
        self.suspends = []

    def show_warehouses(self):
        if self._raise:
            raise self._raise
        return self._rows

    def suspend_warehouse(self, name):
        self.suspends.append(name)
        return SuspendResult(SuspendOutcome.ACCEPTED)

    def close_hard(self):
        self.closed = True


@pytest.mark.asyncio
async def test_successful_tick_runs_cycle():
    store = InMemoryStore()
    store.seed_settings(
        SettingsRow(
            organization_id="org-1",
            agreed_at=NOW,
            global_enabled=True,
            grant_present=True,
            grant_checked_at=NOW,
        )
    )
    store.seed_enrollment(
        EnrollmentRow(
            organization_id="org-1",
            warehouse_name="WH1",
            enabled=True,
            warehouse_created_on=NOW - timedelta(days=1),
            updated_at=NOW - timedelta(minutes=1),
        )
    )
    rows = [
        {
            "name": "WH1",
            "state": "STARTED",
            "type": "STANDARD",
            "started_clusters": 1,
            "min_cluster_count": 1,
            "max_cluster_count": 1,
            "running": 0,
            "queued": 0,
            "quiescing": 0,
            "auto_suspend": 300,
            "auto_resume": "true",
            "resumed_on": NOW.replace(hour=11, minute=58),
            "created_on": NOW - timedelta(days=1),
        }
    ]
    session = FakeSession(rows=rows)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = await run_tenant_once(
            "org-1",
            session=session,
            store=store,
            config=CONFIG,
            executor=executor,
            now_fn=lambda: NOW,
            unknown_attempts={},
            lock=asyncio.Lock(),
        )
    assert result is CycleResult.NORMAL
    assert session.suspends == ["WH1"]


@pytest.mark.asyncio
async def test_wedged_session_is_force_closed():
    session = FakeSession(raise_exc=RuntimeError("boom"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(RuntimeError):
            await run_tenant_once(
                "org-1",
                session=session,
                store=InMemoryStore(),
                config=CONFIG,
                executor=executor,
                now_fn=lambda: NOW,
                unknown_attempts={},
                lock=asyncio.Lock(),
            )
    assert session.closed is True


@pytest.mark.asyncio
async def test_timed_out_tick_drains_abandoned_thread_before_returning():
    # #2a: on timeout the pool thread running _tick can't be cancelled from
    # Python. run_tenant_once must close_hard() (freeing the wedged thread) AND
    # wait for that thread to terminate before returning, so the next tick never
    # overlaps the abandoned thread on the same session.
    import threading

    class DrainProbe(FakeSession):
        def __init__(self):
            super().__init__()
            self._release = threading.Event()
            self.finished = threading.Event()

        def show_warehouses(self):
            self._release.wait(5)  # blocks past the 0.2s watchdog
            self.finished.set()  # only reachable AFTER close_hard() releases us
            return []

        def close_hard(self):
            super().close_hard()
            self._release.set()  # socket close frees the blocked recv

    cfg = WorkerConfig(
        supabase_url="u",
        supabase_service_role_key="k",
        poll_timeout_seconds=0.2,
        socket_timeout_seconds=0.05,
    )
    session = DrainProbe()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await run_tenant_once(
                "org-1",
                session=session,
                store=InMemoryStore(),
                config=cfg,
                executor=executor,
                now_fn=lambda: NOW,
                unknown_attempts={},
                lock=asyncio.Lock(),
            )
    # Drained: the abandoned thread ran to completion before we returned. Without
    # the drain, finished could still be unset here (thread still running).
    assert session.finished.is_set() is True
    assert session.closed is True


@pytest.mark.asyncio
async def test_timed_out_tick_consumes_released_worker_exception():
    class RaisingAfterCloseSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self._release = threading.Event()

        def show_warehouses(self):
            self.started.set()
            self._release.wait(5)
            raise RuntimeError("worker failed after release")

        def close_hard(self):
            super().close_hard()
            self._release.set()

    cfg = WorkerConfig(
        supabase_url="u",
        supabase_service_role_key="k",
        poll_timeout_seconds=0.05,
        socket_timeout_seconds=0.01,
    )
    session = RaisingAfterCloseSession()
    loop = asyncio.get_running_loop()
    unhandled = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                await run_tenant_once(
                    "org-1",
                    session=session,
                    store=InMemoryStore(),
                    config=cfg,
                    executor=executor,
                    now_fn=lambda: NOW,
                    unknown_attempts={},
                    lock=asyncio.Lock(),
                )
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert session.started.is_set()
    assert not [
        context
        for context in unhandled
        if context.get("message") == "Future exception was never retrieved"
    ]


@pytest.mark.asyncio
async def test_timed_out_queued_tick_is_cancelled_before_it_can_run(monkeypatch):
    blocker_started = threading.Event()
    release_blocker = threading.Event()
    cycle_calls = 0

    def count_cycle(*_args, **_kwargs):
        nonlocal cycle_calls
        cycle_calls += 1
        return False

    monkeypatch.setattr(tenant_loop_mod, "run_cycle", count_cycle)

    def occupy_only_worker():
        blocker_started.set()
        release_blocker.wait(5)

    class QueuedSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.show_calls = 0

        def show_warehouses(self):
            self.show_calls += 1
            return []

    cfg = WorkerConfig(
        supabase_url="u",
        supabase_service_role_key="k",
        poll_timeout_seconds=0.05,
        socket_timeout_seconds=0.01,
    )
    session = QueuedSession()
    with ThreadPoolExecutor(max_workers=1) as executor:
        blocker = executor.submit(occupy_only_worker)
        assert blocker_started.wait(1)
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await run_tenant_once(
                "org-1",
                session=session,
                store=InMemoryStore(),
                config=cfg,
                executor=executor,
                now_fn=lambda: NOW,
                unknown_attempts={},
                lock=asyncio.Lock(),
            )
        release_blocker.set()
        blocker.result(timeout=1)

    assert session.show_calls == 0
    assert cycle_calls == 0
    assert session.closed is False


class ScriptedSession(FakeSession):
    """show_warehouses() succeeds/fails per a scripted sequence."""

    def __init__(self, script):
        super().__init__(rows=[])
        self._script = list(script)
        self._i = 0

    def show_warehouses(self):
        outcome = self._script[self._i]
        self._i += 1
        if outcome == "fail":
            raise RuntimeError("boom")
        return []


async def _run_loop(session, *, stop_after, jitter=lambda: 1.0, store=None):
    """Drive tenant_loop for exactly ``stop_after`` ticks; return recorded sleeps."""
    sleeps: list[float] = []
    stop = asyncio.Event()

    async def fake_sleep(delay):
        sleeps.append(delay)
        if len(sleeps) >= stop_after:
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await tenant_loop(
            "org-1",
            session=session,
            store=store if store is not None else InMemoryStore(),
            config=CONFIG,
            executor=executor,
            sleep=fake_sleep,
            stop=stop,
            now_fn=lambda: NOW,
            jitter=jitter,
        )
    return sleeps


@pytest.mark.asyncio
async def test_loop_failure_sleeps_backoff_and_increments():
    # jitter=1.0 → next_backoff(0)=0.5, next_backoff(1)=1.0 (increments each fail).
    sleeps = await _run_loop(ScriptedSession(["fail", "fail"]), stop_after=2)
    assert sleeps == [0.5, 1.0]


@pytest.mark.asyncio
async def test_loop_reconnects_on_tick_after_actual_failure(monkeypatch):
    class RecoveringSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.show_calls = 0
            self.close_calls = 0
            self.reconnects = 0

        def show_warehouses(self):
            self.show_calls += 1
            if self.show_calls == 1:
                raise RuntimeError("lost connection")
            if self.closed:
                self.reconnects += 1
                self.closed = False
            return []

        def close_hard(self):
            self.close_calls += 1
            super().close_hard()

    monkeypatch.setattr(
        tenant_loop_mod,
        "run_cycle",
        lambda *_args, **_kwargs: CycleResult.NORMAL,
    )
    session = RecoveringSession()

    await _run_loop(session, stop_after=2, jitter=lambda: 1.0)

    assert session.show_calls == 2
    assert session.close_calls == 2
    assert session.reconnects == 1


@pytest.mark.asyncio
async def test_loop_success_sleeps_interval_and_resets_backoff():
    # fail → success → fail. Success sleeps poll_interval and resets attempt, so the
    # trailing failure backs off from attempt 0 again, not attempt 1.
    # jitter=0.5 → success cadence factor 0.85+0.30*0.5 = 1.0 (exactly poll_interval);
    # next_backoff(0)=0.5*0.5=0.25 both times (reset proven by the two equal backoffs).
    sleeps = await _run_loop(
        ScriptedSession(["fail", "ok", "fail"]), stop_after=3, jitter=lambda: 0.5
    )
    assert sleeps == [0.25, CONFIG.poll_interval_seconds, 0.25]


@pytest.mark.asyncio
async def test_retry_result_uses_backoff_without_closing_session(monkeypatch):
    results = iter([CycleResult.RETRY_BACKOFF, CycleResult.RETRY_BACKOFF])
    monkeypatch.setattr(
        tenant_loop_mod,
        "run_cycle",
        lambda *_args, **_kwargs: next(results),
    )
    session = FakeSession()

    sleeps = await _run_loop(session, stop_after=2, jitter=lambda: 1.0)

    assert sleeps == [0.5, 1.0]
    assert session.closed is False


@pytest.mark.asyncio
async def test_loop_reuses_one_unknown_attempt_map_across_cycles(monkeypatch):
    seen: list[dict[str, str]] = []

    def capture_map(*_args, unknown_attempts, **_kwargs):
        seen.append(unknown_attempts)
        unknown_attempts.setdefault("WH1", "attempt-1")
        return CycleResult.NORMAL

    monkeypatch.setattr(tenant_loop_mod, "run_cycle", capture_map)

    await _run_loop(FakeSession(), stop_after=2, jitter=lambda: 0.5)

    assert len(seen) == 2
    assert seen[0] is seen[1]
    assert seen[1] == {"WH1": "attempt-1"}


@pytest.mark.asyncio
async def test_steady_state_sleep_falls_within_jittered_bounds():
    # Finding #17: the ±15% jitter on steady-state (no-intent) cadence is
    # deliberate (fleet phase-lock avoidance, see the comment in tenant_loop.py),
    # not an oversight. Prove the sleep duration stays within
    # [0.85, 1.15] * poll_interval_seconds across the jitter() range.
    low = await _run_loop(ScriptedSession(["ok"]), stop_after=1, jitter=lambda: 0.0)
    high = await _run_loop(ScriptedSession(["ok"]), stop_after=1, jitter=lambda: 1.0)
    assert low == [pytest.approx(CONFIG.poll_interval_seconds * 0.85)]
    assert high == [pytest.approx(CONFIG.poll_interval_seconds * 1.15)]


class DrainableSession:
    def __init__(self):
        self.closed = False

    def close_hard(self):
        self.closed = True


@pytest.fixture
def stub_tenant_loop(monkeypatch):
    """Replace the real per-tenant loop with one that just parks on its stop event."""
    started: list[str] = []

    async def fake_loop(org_id, *, session, store, config, executor, sleep, stop):
        started.append(org_id)
        await stop.wait()

    monkeypatch.setattr(tenant_loop_mod, "tenant_loop", fake_loop)
    return started


class _ScriptedStore:
    def __init__(self, state):
        self._state = state

    def worker_tenants(self):
        return list(self._state["tenants"])


@pytest.mark.asyncio
async def test_supervisor_starts_newcomers_and_drains_vanished(stub_tenant_loop):
    state = {"tenants": {"org-1"}}
    store = _ScriptedStore(state)
    sessions: dict[str, DrainableSession] = {}
    stop = asyncio.Event()
    step = {"n": 0}

    def factory(org_id):
        sessions[org_id] = DrainableSession()
        return sessions[org_id], None

    async def fake_sleep(_delay):
        step["n"] += 1
        if step["n"] == 1:
            state["tenants"] = set()  # org-1 vanishes → should be drained + closed
        else:
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=store,
            config=CONFIG,
            executor=executor,
            session_factory=factory,
            sleep=fake_sleep,
            stop=stop,
        )

    assert stub_tenant_loop == ["org-1"]
    assert sessions["org-1"].closed is True


@pytest.mark.asyncio
async def test_supervisor_isolates_a_failing_session_factory(stub_tenant_loop):
    # D1 regression: a factory that raises for one org must not stop the others
    # from starting nor crash the refresh loop.
    state = {"tenants": {"org-good", "org-bad"}}
    store = _ScriptedStore(state)
    sessions: dict[str, DrainableSession] = {}
    stop = asyncio.Event()

    def factory(org_id):
        if org_id == "org-bad":
            raise RuntimeError("no snowflake config")
        sessions[org_id] = DrainableSession()
        return sessions[org_id], None

    async def fake_sleep(_delay):
        stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=store,
            config=CONFIG,
            executor=executor,
            session_factory=factory,
            sleep=fake_sleep,
            stop=stop,
        )

    assert stub_tenant_loop == [
        "org-good"
    ]  # good tenant started despite bad one failing
    assert "org-bad" not in sessions


@pytest.mark.asyncio
async def test_supervisor_keeps_session_when_fingerprint_unchanged(stub_tenant_loop):
    # #3: a stable connection fingerprint must NOT recreate the warm session.
    state = {"tenants": {"org-1"}}
    store = _ScriptedStore(state)
    sessions: list[DrainableSession] = []
    stop = asyncio.Event()
    step = {"n": 0}

    def factory(org_id):
        s = DrainableSession()
        sessions.append(s)
        # session + fingerprint from ONE resolve (finding #2)
        return s, fingerprint(org_id)

    def fingerprint(_org_id):
        return "fp-stable"

    async def fake_sleep(_delay):
        step["n"] += 1
        if step["n"] >= 2:  # let at least one revalidation pass run
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=store,
            config=CONFIG,
            executor=executor,
            session_factory=factory,
            fingerprint_fn=fingerprint,
            sleep=fake_sleep,
            stop=stop,
        )

    assert len(sessions) == 1  # never recreated across refreshes
    assert sessions[0].closed is True  # closed only on shutdown drain


@pytest.mark.asyncio
async def test_supervisor_recycles_session_when_fingerprint_changes(stub_tenant_loop):
    # #3: a rotated account/credential (changed fingerprint) must close the old
    # warm session and create a fresh one.
    state = {"tenants": {"org-1"}}
    store = _ScriptedStore(state)
    sessions: list[DrainableSession] = []
    stop = asyncio.Event()
    step = {"n": 0}

    def factory(org_id):
        s = DrainableSession()
        sessions.append(s)
        # session + fingerprint from ONE resolve (finding #2)
        return s, fingerprint(org_id)

    def fingerprint(_org_id):
        # Stable on the first refresh (start + its revalidation), rotates after.
        return "fp-A" if step["n"] == 0 else "fp-B"

    async def fake_sleep(_delay):
        step["n"] += 1
        if step["n"] >= 2:
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=store,
            config=CONFIG,
            executor=executor,
            session_factory=factory,
            fingerprint_fn=fingerprint,
            sleep=fake_sleep,
            stop=stop,
        )

    assert len(sessions) == 2  # old recycled, new created
    assert sessions[0].closed is True  # old session closed on recycle
    assert sessions[1].closed is True  # new session closed on shutdown


@pytest.mark.asyncio
async def test_supervisor_drops_session_when_connection_disappears(stub_tenant_loop):
    # #3: if the connection resolve raises OrgConnectionNotConfiguredError, the
    # warm session is dropped (like a vanished tenant) rather than kept.
    from greysight_connect.org_connection_resolver import (
        OrgConnectionNotConfiguredError,
    )

    state = {"tenants": {"org-1"}}
    store = _ScriptedStore(state)
    sessions: list[DrainableSession] = []
    stop = asyncio.Event()
    step = {"n": 0}

    def factory(org_id):
        s = DrainableSession()
        sessions.append(s)
        # session + fingerprint from ONE resolve (finding #2)
        return s, fingerprint(org_id)

    def fingerprint(_org_id):
        if step["n"] == 0:
            return "fp-A"
        raise OrgConnectionNotConfiguredError("disconnected")

    async def fake_sleep(_delay):
        step["n"] += 1
        if step["n"] >= 2:
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=store,
            config=CONFIG,
            executor=executor,
            session_factory=factory,
            fingerprint_fn=fingerprint,
            sleep=fake_sleep,
            stop=stop,
        )

    assert len(sessions) == 1  # never re-created while connection stays gone
    assert sessions[0].closed is True  # dropped/closed on the revalidation pass


@pytest.mark.asyncio
async def test_supervisor_restarts_crashed_task(monkeypatch, caplog):
    # #12b: a tenant loop task that exits unexpectedly must be detected (.done())
    # and restarted so the tenant does not silently go dark.
    starts: list[str] = []

    async def crashing_then_parking_loop(
        org_id, *, session, store, config, executor, sleep, stop
    ):
        starts.append(org_id)
        if len(starts) == 1:
            raise RuntimeError("loop crashed")  # first instance dies
        await stop.wait()  # restarted instance parks

    monkeypatch.setattr(tenant_loop_mod, "tenant_loop", crashing_then_parking_loop)

    state = {"tenants": {"org-1"}}
    store = _ScriptedStore(state)
    sessions: list[DrainableSession] = []
    stop = asyncio.Event()
    step = {"n": 0}

    def factory(org_id):
        s = DrainableSession()
        sessions.append(s)
        return s, None

    async def fake_sleep(_delay):
        await asyncio.sleep(0)  # let the scheduled tenant task run (and crash)
        step["n"] += 1
        if step["n"] >= 2:
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=store,
            config=CONFIG,
            executor=executor,
            session_factory=factory,
            sleep=fake_sleep,
            stop=stop,
        )

    assert starts == ["org-1", "org-1"]  # crashed loop detected + restarted
    assert len(sessions) == 2
    assert sessions[0].closed is True  # crashed session closed on restart
    crash_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "tenant_loop_crashed"
    ]
    assert len(crash_records) == 1
    assert crash_records[0].organization_id == "org-1"
    assert crash_records[0].error_type == "RuntimeError"
    assert crash_records[0].exc_info is None


@pytest.mark.asyncio
async def test_drain_awaits_bounded_store_call_to_completion():
    # CRITICAL: the abandoned _tick thread can be blocked in a Supabase STORE
    # call (run_cycle → list_enrollments), which close_hard() (Snowflake socket)
    # does NOT unblock — only the store's own request timeout does. The drain
    # must await the future to ACTUAL completion (guaranteed prompt because the
    # store call is bounded by store_timeout_seconds) before returning, so the
    # next tick can never overlap the abandoned thread on the same session.
    import threading

    store_unblocked = threading.Event()
    tick_finished = threading.Event()

    class BoundedStore(InMemoryStore):
        def list_enrollments(self, organization_id):
            # Model an in-flight Supabase request bounded by the httpx request
            # timeout: it frees ITSELF (store timeout) — NOT via close_hard. The
            # timer stands in for store_timeout_seconds firing.
            threading.Timer(0.4, store_unblocked.set).start()
            store_unblocked.wait(5)
            tick_finished.set()  # reached only after the store call unblocks
            return []

    # poll_timeout (0.2) fires BEFORE the store unblocks (0.4): wait_for times
    # out, close_hard() runs (does not free the store call), then the guaranteed
    # drain must wait for the store timeout at 0.4 and the tick to finish.
    cfg = WorkerConfig(
        supabase_url="u",
        supabase_service_role_key="k",
        poll_timeout_seconds=0.2,
        socket_timeout_seconds=0.05,
    )
    session = FakeSession(rows=[])
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await run_tenant_once(
                "org-1",
                session=session,
                store=BoundedStore(),
                config=cfg,
                executor=executor,
                now_fn=lambda: NOW,
                unknown_attempts={},
                lock=asyncio.Lock(),
            )
    # The abandoned thread ran to completion BEFORE run_tenant_once returned:
    # without the guaranteed drain, tick_finished could still be unset here.
    assert tick_finished.is_set() is True
    assert session.closed is True


@pytest.mark.asyncio
async def test_supervisor_keeps_session_on_transient_revalidation_error(
    stub_tenant_loop,
):
    # IMPORTANT-1: a TRANSIENT resolve failure (OrgConnectionUnavailableError)
    # during revalidation must KEEP the still-configured warm session, not drop
    # it like a genuinely-gone connection.
    from greysight_connect.org_connection_resolver import (
        OrgConnectionUnavailableError,
    )

    state = {"tenants": {"org-1"}}
    store = _ScriptedStore(state)
    sessions: list[DrainableSession] = []
    stop = asyncio.Event()
    step = {"n": 0}

    def factory(org_id):
        s = DrainableSession()
        sessions.append(s)
        return s, fingerprint(org_id)

    def fingerprint(_org_id):
        if step["n"] == 0:
            return "fp-A"
        raise OrgConnectionUnavailableError("supabase timeout")

    async def fake_sleep(_delay):
        step["n"] += 1
        if step["n"] >= 2:
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=store,
            config=CONFIG,
            executor=executor,
            session_factory=factory,
            fingerprint_fn=fingerprint,
            sleep=fake_sleep,
            stop=stop,
        )

    assert len(sessions) == 1  # kept across the transient blip, never recreated
    assert sessions[0].closed is True  # closed only on the shutdown drain


@pytest.mark.asyncio
async def test_start_uses_single_resolve_for_session_and_fingerprint(
    stub_tenant_loop,
):
    # IMPORTANT-2: the session AND its fingerprint must come from ONE resolve
    # (the factory), never a second separate read that could disagree after a
    # rotation and pin the old session to the new fingerprint forever.
    state = {"tenants": {"org-1"}}
    store = _ScriptedStore(state)
    resolves = {"n": 0}
    sessions: list[DrainableSession] = []

    def factory(org_id):
        resolves["n"] += 1  # ONE resolve → session + fingerprint together
        s = DrainableSession()
        sessions.append(s)
        return s, f"fp-{resolves['n']}"

    def fingerprint(_org_id):
        return "fp-1"  # revalidation sees the SAME fingerprint the session holds

    stop = asyncio.Event()
    step = {"n": 0}

    async def fake_sleep(_delay):
        step["n"] += 1
        if step["n"] >= 2:  # allow a revalidation pass
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=store,
            config=CONFIG,
            executor=executor,
            session_factory=factory,
            fingerprint_fn=fingerprint,
            sleep=fake_sleep,
            stop=stop,
        )

    assert resolves["n"] == 1  # session built from EXACTLY one resolve
    assert len(sessions) == 1  # fingerprint consistent → never recycled
    assert sessions[0].closed is True


@pytest.mark.asyncio
async def test_revalidation_runs_off_the_event_loop_thread(stub_tenant_loop):
    # IMPORTANT-3: fingerprint revalidation does SYNC network I/O; it must run on
    # the executor, not inline on the asyncio event loop thread.
    import threading

    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def factory(org_id):
        return DrainableSession(), "fp"

    def fingerprint(_org_id):
        seen["thread"] = threading.get_ident()
        return "fp"

    stop = asyncio.Event()
    step = {"n": 0}

    async def fake_sleep(_delay):
        step["n"] += 1
        if step["n"] >= 2:  # allow a revalidation pass to run
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=_ScriptedStore({"tenants": {"org-1"}}),
            config=CONFIG,
            executor=executor,
            session_factory=factory,
            fingerprint_fn=fingerprint,
            sleep=fake_sleep,
            stop=stop,
        )

    assert "thread" in seen  # revalidation actually ran
    assert seen["thread"] != loop_thread  # ...off the event loop, on the executor


@pytest.mark.asyncio
async def test_timeout_closes_connection_reopened_after_store_stall(monkeypatch):
    release_store = threading.Event()

    class StallingStore(InMemoryStore):
        def wait_for_store(self):
            threading.Timer(0.1, release_store.set).start()
            release_store.wait(5)

    class ReopeningSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.connected = False
            self.connect_count = 0
            self.close_count = 0

        def _ensure_connected(self):
            if not self.connected:
                self.connected = True
                self.connect_count += 1

        def show_warehouses(self):
            self._ensure_connected()
            return []

        def suspend_warehouse(self, name):
            self._ensure_connected()
            return super().suspend_warehouse(name)

        def close_hard(self):
            self.close_count += 1
            self.connected = False

    def stalled_cycle(*_args, store, suspend, **_kwargs):
        store.wait_for_store()
        suspend("WH1")
        return CycleResult.NORMAL

    monkeypatch.setattr(tenant_loop_mod, "run_cycle", stalled_cycle)
    cfg = WorkerConfig(
        supabase_url="u",
        supabase_service_role_key="k",
        poll_timeout_seconds=0.05,
        socket_timeout_seconds=0.01,
    )
    session = ReopeningSession()
    lock = asyncio.Lock()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await run_tenant_once(
                "org-1",
                session=session,
                store=StallingStore(),
                config=cfg,
                executor=executor,
                now_fn=lambda: NOW,
                unknown_attempts={},
                lock=lock,
            )

        assert session.suspends == ["WH1"]
        assert session.connected is False
        assert session.close_count == 2

        monkeypatch.setattr(
            tenant_loop_mod,
            "run_cycle",
            lambda *_args, **_kwargs: CycleResult.NORMAL,
        )
        await run_tenant_once(
            "org-1",
            session=session,
            store=InMemoryStore(),
            config=cfg,
            executor=executor,
            now_fn=lambda: NOW,
            unknown_attempts={},
            lock=lock,
        )

    assert session.connect_count == 3


@pytest.mark.parametrize("result", [CycleResult.NORMAL, CycleResult.RETRY_BACKOFF])
@pytest.mark.asyncio
async def test_tenant_stop_interrupts_cadence_wait(monkeypatch, result):
    sleep_started = asyncio.Event()
    never = asyncio.Event()

    async def blocking_sleep(_delay):
        sleep_started.set()
        await never.wait()

    monkeypatch.setattr(
        tenant_loop_mod,
        "run_cycle",
        lambda *_args, **_kwargs: result,
    )
    stop = asyncio.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        task = asyncio.create_task(
            tenant_loop(
                "org-1",
                session=FakeSession(),
                store=InMemoryStore(),
                config=CONFIG,
                executor=executor,
                sleep=blocking_sleep,
                stop=stop,
            )
        )
        await asyncio.wait_for(sleep_started.wait(), timeout=1)
        stop.set()
        await asyncio.wait_for(task, timeout=0.2)


@pytest.mark.asyncio
async def test_shutdown_signals_all_tenants_before_concurrent_drain(monkeypatch):
    signaled: set[str] = set()

    async def wait_for_all_stops(
        org_id, *, session, store, config, executor, sleep, stop
    ):
        await stop.wait()
        signaled.add(org_id)
        while len(signaled) < 2:
            await asyncio.sleep(0)

    monkeypatch.setattr(tenant_loop_mod, "tenant_loop", wait_for_all_stops)
    stop = asyncio.Event()

    async def stop_supervisor(_delay):
        stop.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        await asyncio.wait_for(
            supervisor(
                store=_ScriptedStore({"tenants": {"org-1", "org-2"}}),
                config=CONFIG,
                executor=executor,
                session_factory=lambda _org_id: (DrainableSession(), None),
                sleep=stop_supervisor,
                stop=stop,
            ),
            timeout=0.2,
        )

    assert signaled == {"org-1", "org-2"}


@pytest.mark.asyncio
async def test_removal_signals_all_vanished_tenants_before_concurrent_drain(
    monkeypatch,
):
    signaled: set[str] = set()

    async def wait_for_all_stops(
        org_id, *, session, store, config, executor, sleep, stop
    ):
        await stop.wait()
        signaled.add(org_id)
        while len(signaled) < 2:
            await asyncio.sleep(0)

    monkeypatch.setattr(tenant_loop_mod, "tenant_loop", wait_for_all_stops)
    state = {"tenants": {"org-1", "org-2"}}
    stop = asyncio.Event()
    sleeps = 0

    async def advance_supervisor(_delay):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            state["tenants"] = set()
        else:
            stop.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        await asyncio.wait_for(
            supervisor(
                store=_ScriptedStore(state),
                config=CONFIG,
                executor=executor,
                session_factory=lambda _org_id: (DrainableSession(), None),
                sleep=advance_supervisor,
                stop=stop,
            ),
            timeout=0.2,
        )

    assert signaled == {"org-1", "org-2"}


@pytest.mark.asyncio
async def test_wait_or_stop_cancellation_drains_internal_tasks():
    sleep_started = asyncio.Event()
    release_sleep = asyncio.Event()
    stop = asyncio.Event()

    async def blocking_sleep(_delay):
        sleep_started.set()
        await release_sleep.wait()

    baseline = set(asyncio.all_tasks())
    waiter = asyncio.create_task(
        tenant_loop_mod._wait_or_stop(stop, 30, blocking_sleep)
    )
    await asyncio.wait_for(sleep_started.wait(), timeout=1)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await asyncio.sleep(0)

    leaked = {
        task
        for task in asyncio.all_tasks() - baseline
        if task is not asyncio.current_task() and not task.done()
    }
    try:
        assert leaked == set()
    finally:
        release_sleep.set()
        stop.set()
        await asyncio.gather(*leaked, return_exceptions=True)


@pytest.mark.asyncio
async def test_wait_or_stop_propagates_completed_sleep_error():
    class SleepFailure(RuntimeError):
        pass

    async def failing_sleep(_delay):
        raise SleepFailure

    with pytest.raises(SleepFailure):
        await tenant_loop_mod._wait_or_stop(asyncio.Event(), 1, failing_sleep)


@pytest.mark.asyncio
async def test_tick_failure_logs_sanitized_backoff_context(caplog):
    caplog.set_level("WARNING")

    await _run_loop(
        ScriptedSession(["fail"]),
        stop_after=1,
        jitter=lambda: 1.0,
    )

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "tenant_tick_backoff"
    ]
    assert len(records) == 1
    assert records[0].organization_id == "org-1"
    assert records[0].attempt == 0
    assert records[0].error_type == "RuntimeError"
    assert records[0].exc_info is None


@pytest.mark.asyncio
async def test_tenant_enumeration_failure_logs_sanitized_context(caplog):
    class FailingStore:
        def worker_tenants(self):
            raise RuntimeError("secret response body")

    caplog.set_level("WARNING")
    stop = asyncio.Event()

    async def stop_supervisor(_delay):
        stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=FailingStore(),
            config=CONFIG,
            executor=executor,
            session_factory=lambda _org_id: (DrainableSession(), None),
            sleep=stop_supervisor,
            stop=stop,
        )

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "tenant_enumeration_failed"
    ]
    assert len(records) == 1
    assert records[0].error_type == "RuntimeError"
    assert records[0].exc_info is None
    assert "secret response body" not in records[0].getMessage()


@pytest.mark.asyncio
async def test_tenant_enumeration_runs_on_executor_thread():
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    class TrackingStore:
        def worker_tenants(self):
            seen["thread"] = threading.get_ident()
            return []

    stop = asyncio.Event()

    async def stop_supervisor(_delay):
        stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=TrackingStore(),
            config=CONFIG,
            executor=executor,
            session_factory=lambda _org_id: (DrainableSession(), None),
            sleep=stop_supervisor,
            stop=stop,
        )

    assert seen["thread"] != loop_thread


@pytest.mark.asyncio
async def test_session_factory_runs_on_executor_thread(stub_tenant_loop):
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    stop = asyncio.Event()

    def factory(_org_id):
        seen["thread"] = threading.get_ident()
        return DrainableSession(), None

    async def stop_supervisor(_delay):
        stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(
            store=_ScriptedStore({"tenants": {"org-1"}}),
            config=CONFIG,
            executor=executor,
            session_factory=factory,
            sleep=stop_supervisor,
            stop=stop,
        )

    assert seen["thread"] != loop_thread


def test_tenant_lock_registry_does_not_accumulate_organization_ids():
    assert not hasattr(tenant_loop_mod, "_locks")


@pytest.mark.asyncio
async def test_shared_tenant_lock_prevents_overlapping_ticks(monkeypatch):
    first_started = threading.Event()
    release_first = threading.Event()

    class SerializedSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.show_calls = 0

        def show_warehouses(self):
            self.show_calls += 1
            if self.show_calls == 1:
                first_started.set()
                release_first.wait(5)
            return []

    monkeypatch.setattr(
        tenant_loop_mod,
        "run_cycle",
        lambda *_args, **_kwargs: CycleResult.NORMAL,
    )
    session = SerializedSession()
    lock = asyncio.Lock()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = asyncio.create_task(
            run_tenant_once(
                "org-1",
                session=session,
                store=InMemoryStore(),
                config=CONFIG,
                executor=executor,
                now_fn=lambda: NOW,
                unknown_attempts={},
                lock=lock,
            )
        )
        assert await asyncio.to_thread(first_started.wait, 1)
        second = asyncio.create_task(
            run_tenant_once(
                "org-1",
                session=session,
                store=InMemoryStore(),
                config=CONFIG,
                executor=executor,
                now_fn=lambda: NOW,
                unknown_attempts={},
                lock=lock,
            )
        )
        await asyncio.sleep(0.05)
        assert session.show_calls == 1
        release_first.set()
        await asyncio.gather(first, second)

    assert session.show_calls == 2

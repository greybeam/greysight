import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

import auto_savings.tenant_loop as tenant_loop_mod
from auto_savings.config import WorkerConfig
from auto_savings.store import EnrollmentRow, InMemoryStore, SettingsRow
from auto_savings.tenant_loop import run_tenant_once, supervisor, tenant_loop

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = WorkerConfig(supabase_url="u", supabase_service_role_key="k")


class FakeSession:
    def __init__(self, rows=None, raise_exc=None):
        self._rows = rows or []
        self._raise = raise_exc
        self.closed = False
        self.alters = []

    def show_warehouses(self):
        if self._raise:
            raise self._raise
        return self._rows

    def alter_auto_suspend(self, name, value):
        self.alters.append((name, value))

    def close_hard(self):
        self.closed = True


@pytest.mark.asyncio
async def test_successful_tick_runs_cycle():
    store = InMemoryStore()
    store.seed_settings(SettingsRow(organization_id="org-1", agreed_at=NOW,
                                    global_enabled=True, grant_present=True,
                                    grant_checked_at=NOW))
    store.seed_enrollment(EnrollmentRow(
        organization_id="org-1", warehouse_name="WH1", enabled=True,
        managed_auto_suspend=300, stored_default_auto_suspend=300,
        warehouse_created_on=NOW, cooldown_ts=None, drift_state="ok", drifted_value=None))
    rows = [{"name": "WH1", "state": "STARTED", "type": "STANDARD",
             "started_clusters": 1, "min_cluster_count": 1, "max_cluster_count": 1,
             "running": 0, "queued": 0, "auto_suspend": 300, "auto_resume": "true",
             "resumed_on": NOW.replace(hour=11, minute=58)}]
    session = FakeSession(rows=rows)
    with ThreadPoolExecutor(max_workers=1) as executor:
        await run_tenant_once("org-1", session=session, store=store, config=CONFIG,
                              executor=executor, now_fn=lambda: NOW)
    assert session.alters == [("WH1", 1)]


@pytest.mark.asyncio
async def test_wedged_session_is_force_closed():
    session = FakeSession(raise_exc=RuntimeError("boom"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(RuntimeError):
            await run_tenant_once("org-1", session=session, store=InMemoryStore(),
                                  config=CONFIG, executor=executor, now_fn=lambda: NOW)
    assert session.closed is True


@pytest.mark.asyncio
async def test_genuinely_blocking_call_times_out_and_frees_the_loop():
    # A show_warehouses that blocks past poll_timeout must not wedge the loop.
    # (The connector socket timeout frees the pool thread in prod; here we prove the
    #  wait_for path raises and close_hard() runs.)
    import threading

    class BlockingSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self._release = threading.Event()

        def show_warehouses(self):
            self.started.set()
            # Blocks past the 0.2s timeout; close_hard() releases it, mirroring a
            # socket close freeing a wedged recv (so the drain stays fast).
            self._release.wait(5)
            return []

        def close_hard(self):
            super().close_hard()
            self._release.set()

    # socket_timeout must stay strictly < poll_timeout (WorkerConfig invariant) and,
    # since config validation now requires every interval to be finite and strictly
    # positive (finding #9/#16), a small positive value keeps the fast 0.2s watchdog
    # valid; the FakeSession ignores it either way.
    cfg = WorkerConfig(supabase_url="u", supabase_service_role_key="k",
                       poll_timeout_seconds=0.2, socket_timeout_seconds=0.05)
    session = BlockingSession()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await run_tenant_once("org-1", session=session, store=InMemoryStore(),
                                  config=cfg, executor=executor, now_fn=lambda: NOW)
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

    cfg = WorkerConfig(supabase_url="u", supabase_service_role_key="k",
                       poll_timeout_seconds=0.2, socket_timeout_seconds=0.05)
    session = DrainProbe()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await run_tenant_once("org-1", session=session, store=InMemoryStore(),
                                  config=cfg, executor=executor, now_fn=lambda: NOW)
    # Drained: the abandoned thread ran to completion before we returned. Without
    # the drain, finished could still be unset here (thread still running).
    assert session.finished.is_set() is True
    assert session.closed is True


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
            "org-1", session=session,
            store=store if store is not None else InMemoryStore(), config=CONFIG,
            executor=executor, sleep=fake_sleep, stop=stop, now_fn=lambda: NOW,
            jitter=jitter,
        )
    return sleeps


@pytest.mark.asyncio
async def test_loop_failure_sleeps_backoff_and_increments():
    # jitter=1.0 → next_backoff(0)=0.5, next_backoff(1)=1.0 (increments each fail).
    sleeps = await _run_loop(ScriptedSession(["fail", "fail"]), stop_after=2)
    assert sleeps == [0.5, 1.0]


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


def _idle_sentinel_rows():
    # STARTED & idle & live==1 with resumed_on matching the seeded baseline → HELD,
    # so the intent survives the cycle and run_cycle reports intents outstanding.
    return [{"name": "WH1", "state": "STARTED", "type": "STANDARD",
             "started_clusters": 1, "min_cluster_count": 1, "max_cluster_count": 1,
             "running": 0, "queued": 0, "auto_suspend": 1, "auto_resume": "true",
             "resumed_on": NOW.replace(hour=11, minute=59)}]


@pytest.mark.asyncio
async def test_success_fast_polls_while_intent_outstanding():
    store = InMemoryStore()
    store.seed_enrollment(EnrollmentRow(
        organization_id="org-1", warehouse_name="WH1", enabled=True,
        managed_auto_suspend=300, stored_default_auto_suspend=300,
        warehouse_created_on=NOW, cooldown_ts=None, drift_state="ok", drifted_value=None))
    store.write_intent("org-1", "WH1", restore_to=300, set_at=NOW,
                       baseline_resumed_on=NOW.replace(hour=11, minute=59))
    session = FakeSession(rows=_idle_sentinel_rows())
    # jitter=0.5 → factor exactly 1.0, so sleep == intent_poll_interval_seconds.
    sleeps = await _run_loop(session, stop_after=1, jitter=lambda: 0.5, store=store)
    assert sleeps == [CONFIG.intent_poll_interval_seconds]


@pytest.mark.asyncio
async def test_success_normal_cadence_when_no_intent_outstanding():
    # Empty store, warehouse cycle leaves nothing outstanding → normal interval.
    sleeps = await _run_loop(
        ScriptedSession(["ok"]), stop_after=1, jitter=lambda: 0.5
    )
    assert sleeps == [CONFIG.poll_interval_seconds]


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
        return sessions[org_id]

    async def fake_sleep(_delay):
        step["n"] += 1
        if step["n"] == 1:
            state["tenants"] = set()  # org-1 vanishes → should be drained + closed
        else:
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(store=store, config=CONFIG, executor=executor,
                         session_factory=factory, sleep=fake_sleep, stop=stop)

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
        return sessions[org_id]

    async def fake_sleep(_delay):
        stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(store=store, config=CONFIG, executor=executor,
                         session_factory=factory, sleep=fake_sleep, stop=stop)

    assert stub_tenant_loop == ["org-good"]  # good tenant started despite bad one failing
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
        return s

    def fingerprint(_org_id):
        return "fp-stable"

    async def fake_sleep(_delay):
        step["n"] += 1
        if step["n"] >= 2:  # let at least one revalidation pass run
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(store=store, config=CONFIG, executor=executor,
                         session_factory=factory, fingerprint_fn=fingerprint,
                         sleep=fake_sleep, stop=stop)

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
        return s

    def fingerprint(_org_id):
        # Stable on the first refresh (start + its revalidation), rotates after.
        return "fp-A" if step["n"] == 0 else "fp-B"

    async def fake_sleep(_delay):
        step["n"] += 1
        if step["n"] >= 2:
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(store=store, config=CONFIG, executor=executor,
                         session_factory=factory, fingerprint_fn=fingerprint,
                         sleep=fake_sleep, stop=stop)

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
        return s

    def fingerprint(_org_id):
        if step["n"] == 0:
            return "fp-A"
        raise OrgConnectionNotConfiguredError("disconnected")

    async def fake_sleep(_delay):
        step["n"] += 1
        if step["n"] >= 2:
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(store=store, config=CONFIG, executor=executor,
                         session_factory=factory, fingerprint_fn=fingerprint,
                         sleep=fake_sleep, stop=stop)

    assert len(sessions) == 1  # never re-created while connection stays gone
    assert sessions[0].closed is True  # dropped/closed on the revalidation pass


@pytest.mark.asyncio
async def test_supervisor_restarts_crashed_task(monkeypatch):
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
        return s

    async def fake_sleep(_delay):
        await asyncio.sleep(0)  # let the scheduled tenant task run (and crash)
        step["n"] += 1
        if step["n"] >= 2:
            stop.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        await supervisor(store=store, config=CONFIG, executor=executor,
                         session_factory=factory, sleep=fake_sleep, stop=stop)

    assert starts == ["org-1", "org-1"]  # crashed loop detected + restarted
    assert len(sessions) == 2
    assert sessions[0].closed is True  # crashed session closed on restart

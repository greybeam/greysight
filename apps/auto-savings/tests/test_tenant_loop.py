import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from auto_savings.config import WorkerConfig
from auto_savings.store import EnrollmentRow, InMemoryStore, SettingsRow
from auto_savings.tenant_loop import run_tenant_once

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

        def show_warehouses(self):
            self.started.set()
            threading.Event().wait(5)  # blocks well past the 0.2s timeout
            return []

    # socket_timeout must stay strictly < poll_timeout (WorkerConfig invariant);
    # the FakeSession ignores it, so 0 keeps the fast 0.2s watchdog valid.
    cfg = WorkerConfig(supabase_url="u", supabase_service_role_key="k",
                       poll_timeout_seconds=0.2, socket_timeout_seconds=0)
    session = BlockingSession()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await run_tenant_once("org-1", session=session, store=InMemoryStore(),
                                  config=cfg, executor=executor, now_fn=lambda: NOW)
    assert session.closed is True

from datetime import datetime, timedelta, timezone

from auto_savings.config import WorkerConfig
from auto_savings.engine import run_cycle
from auto_savings.store import EnrollmentRow, InMemoryStore, SettingsRow

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = WorkerConfig(supabase_url="u", supabase_service_role_key="k",
                      cooldown_seconds=300, uptime_floor_seconds=62,
                      poll_interval_seconds=3.0, max_intent_hold_ticks=5)


def _seed_settings(store, global_enabled=True):
    store.seed_settings(SettingsRow(organization_id="org-1", agreed_at=NOW,
                                    global_enabled=global_enabled, grant_present=True,
                                    grant_checked_at=NOW))


def _rows(**overrides):
    row = {
        "name": "WH1", "state": "STARTED", "type": "STANDARD", "size": "X-Small",
        "started_clusters": 1, "min_cluster_count": 1, "max_cluster_count": 1,
        "running": 0, "queued": 0, "auto_suspend": 300, "auto_resume": "true",
        "resumed_on": NOW - timedelta(seconds=90),
    }
    row.update(overrides)
    return [row]


def _seed(store, cooldown_ts=None, drift_state="ok"):
    store.seed_enrollment(EnrollmentRow(
        organization_id="org-1", warehouse_name="WH1", enabled=True,
        managed_auto_suspend=300, stored_default_auto_suspend=300,
        warehouse_created_on=NOW - timedelta(days=1), cooldown_ts=cooldown_ts,
        drift_state=drift_state, drifted_value=None))


def test_idle_warehouse_gets_intent_then_alter_in_order():
    store = InMemoryStore()
    _seed(store); _seed_settings(store)
    calls = []
    has_intents = run_cycle("org-1", rows=_rows(), store=store, config=CONFIG, now=NOW,
                            apply_alter=lambda n, v: calls.append((n, v)))
    # Intent restore target is the LIVE managed default; intent written before the ALTER.
    assert store.list_intents("org-1")[0].restore_to == 300
    assert calls == [("WH1", 1)]
    assert has_intents is True  # outstanding intent → fast-poll


def test_run_cycle_returns_false_when_no_intents_outstanding():
    store = InMemoryStore()
    _seed(store, cooldown_ts=NOW + timedelta(seconds=100)); _seed_settings(store)
    # In cooldown → no new suspend, no intent written → normal cadence.
    has_intents = run_cycle("org-1", rows=_rows(), store=store, config=CONFIG, now=NOW,
                            apply_alter=lambda n, v: None)
    assert store.list_intents("org-1") == []
    assert has_intents is False


def test_kill_switch_off_stops_decide_but_still_drains():
    # global_enabled False → no new suspends, but an outstanding intent still restores.
    store = InMemoryStore()
    _seed(store); _seed_settings(store, global_enabled=False)
    store.write_intent("org-1", "WH1", restore_to=300)
    calls = []
    run_cycle("org-1", rows=_rows(state="SUSPENDED", auto_suspend=1, resumed_on=None),
              store=store, config=CONFIG, now=NOW,
              apply_alter=lambda n, v: calls.append((n, v)))
    assert calls == [("WH1", 300)]          # drained
    # A fresh idle warehouse is NOT suspended while the switch is off.
    store2 = InMemoryStore()
    _seed(store2); _seed_settings(store2, global_enabled=False)
    calls2 = []
    run_cycle("org-1", rows=_rows(), store=store2, config=CONFIG, now=NOW,
              apply_alter=lambda n, v: calls2.append((n, v)))
    assert calls2 == []


def test_busy_warehouse_not_touched():
    store = InMemoryStore()
    _seed(store); _seed_settings(store)
    calls = []
    run_cycle("org-1", rows=_rows(running=1), store=store, config=CONFIG, now=NOW,
              apply_alter=lambda n, v: calls.append((n, v)))
    assert calls == []
    assert store.list_intents("org-1") == []


def test_next_tick_restores_and_sets_cooldown():
    store = InMemoryStore()
    _seed(store); _seed_settings(store)
    calls = []
    # Tick 1: set sentinel.
    run_cycle("org-1", rows=_rows(), store=store, config=CONFIG, now=NOW,
              apply_alter=lambda n, v: calls.append((n, v)))
    # Tick 2: warehouse now suspended; intent outstanding → restore + cooldown.
    later = NOW + timedelta(seconds=3)
    run_cycle("org-1", rows=_rows(state="SUSPENDED", auto_suspend=1, resumed_on=None),
              store=store, config=CONFIG, now=later,
              apply_alter=lambda n, v: calls.append((n, v)))
    assert calls == [("WH1", 1), ("WH1", 300)]  # set, then restore
    assert store.list_intents("org-1") == []
    assert store.list_enrollments("org-1")[0].cooldown_ts == later + timedelta(seconds=300)


def test_cooldown_blocks_reacquire():
    store = InMemoryStore()
    _seed(store, cooldown_ts=NOW + timedelta(seconds=100)); _seed_settings(store)
    calls = []
    run_cycle("org-1", rows=_rows(), store=store, config=CONFIG, now=NOW,
              apply_alter=lambda n, v: calls.append((n, v)))
    assert calls == []


def test_drift_this_tick_closes_race_with_stale_enrollment_snapshot():
    # WH1 drifts THIS tick (live auto_suspend != managed, no prior intent).
    # The engine's top-of-cycle enrollment copy still shows drift_state="ok"
    # (stale — reconcile hasn't written the mark yet when decide reads it),
    # so the per-warehouse ``is_drifted`` guard alone would miss it and
    # should_force_suspend would force-suspend a warehouse we don't own the
    # auto_suspend value of. The skip-gate (name in skip → continue) must
    # catch what reconcile settled this cycle regardless of the stale read.
    store = InMemoryStore()
    _seed(store, drift_state="ok"); _seed_settings(store)
    calls = []
    run_cycle(
        "org-1",
        rows=_rows(auto_suspend=120, state="STARTED", running=0, queued=0),
        store=store, config=CONFIG, now=NOW,
        apply_alter=lambda n, v: calls.append((n, v)),
    )
    assert calls == []  # NOT force-suspended despite the stale "ok" read
    assert store.list_enrollments("org-1")[0].drift_state == "drifted"

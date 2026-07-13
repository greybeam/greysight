from datetime import datetime, timedelta, timezone

from auto_savings.decision import should_force_suspend
from auto_savings.warehouse_snapshot import WarehouseSnapshot, parse_warehouses

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _wh(**overrides) -> WarehouseSnapshot:
    base = dict(
        name="WH1", state="STARTED", type="STANDARD", size="X-Small",
        started_clusters=1, min_cluster_count=1, max_cluster_count=1,
        running=0, queued=0, auto_suspend=300, auto_resume=True,
        resumed_on=NOW - timedelta(seconds=90), created_on=NOW - timedelta(days=1),
    )
    base.update(overrides)
    return WarehouseSnapshot(**base)


def _decide(wh, **kw):
    defaults = dict(now=NOW, uptime_floor_seconds=62, in_cooldown=False,
                    is_drifted=False, has_outstanding_intent=False)
    defaults.update(kw)
    return should_force_suspend(wh, **defaults)


def test_fires_when_all_conditions_hold():
    assert _decide(_wh()) is True


def test_each_precondition_individually_blocks():
    assert _decide(_wh(type="SNOWPARK-OPTIMIZED")) is False
    assert _decide(_wh(state="SUSPENDED")) is False
    assert _decide(_wh(state="RESUMING")) is False
    assert _decide(_wh(started_clusters=2, min_cluster_count=1, max_cluster_count=4)) is False
    assert _decide(_wh(resumed_on=NOW - timedelta(seconds=30))) is False  # uptime < floor
    assert _decide(_wh(resumed_on=None)) is False                          # never resumed
    assert _decide(_wh(running=1)) is False
    assert _decide(_wh(queued=1)) is False
    assert _decide(_wh(auto_resume=False)) is False
    assert _decide(_wh(), in_cooldown=True) is False
    assert _decide(_wh(), is_drifted=True) is False
    assert _decide(_wh(), has_outstanding_intent=True) is False


def test_maximized_fires_when_all_clusters_idle_at_floor():
    # min == max == N, started at N, idle → acts.
    assert _decide(_wh(started_clusters=3, min_cluster_count=3, max_cluster_count=3)) is True


def test_autoscale_above_floor_does_not_fire():
    assert _decide(_wh(started_clusters=3, min_cluster_count=1, max_cluster_count=4)) is False


def test_standard_edition_absent_cluster_columns_fires():
    # Task 0 spike blocking defect: SHOW WAREHOUSES omits started_clusters/
    # min_cluster_count/max_cluster_count entirely on Standard edition. Before the fix,
    # started_clusters defaulted to 0 and min_cluster_count to 1, so the gate's
    # `started_clusters == min_cluster_count` check was always False and the worker never
    # suspended anything on Standard edition. Prove the fix makes this fire.
    row = {
        "name": "WH1", "state": "STARTED", "type": "STANDARD",
        "running": 0, "queued": 0, "auto_suspend": 60, "auto_resume": "true",
        "resumed_on": NOW - timedelta(seconds=90), "created_on": NOW - timedelta(days=5),
    }
    [wh] = parse_warehouses([row], now=NOW)
    assert should_force_suspend(
        wh, now=NOW, uptime_floor_seconds=62,
        in_cooldown=False, is_drifted=False, has_outstanding_intent=False,
    ) is True


def test_present_but_null_started_clusters_fails_closed_on_enterprise_row():
    # started_clusters is PRESENT (Enterprise+ edition emits the column) but null/
    # malformed on this row, while min_cluster_count/max_cluster_count ARE present
    # and valid. This must NOT be treated the same as "column absent" (Standard
    # edition) — defaulting started_clusters to min_cluster_count here would let a
    # scaled-up multi-cluster warehouse slip past the safety gate (finding #4).
    row = {
        "name": "WH1", "state": "STARTED", "type": "STANDARD",
        "started_clusters": None, "min_cluster_count": 1, "max_cluster_count": 4,
        "running": 0, "queued": 0, "auto_suspend": 60, "auto_resume": "true",
        "resumed_on": NOW - timedelta(seconds=90), "created_on": NOW - timedelta(days=5),
    }
    [wh] = parse_warehouses([row], now=NOW)
    assert should_force_suspend(
        wh, now=NOW, uptime_floor_seconds=62,
        in_cooldown=False, is_drifted=False, has_outstanding_intent=False,
    ) is False


def test_present_multi_cluster_started_clusters_still_protected():
    # Present started_clusters must still be honored (not overridden by min default),
    # so a scaled-up Enterprise+ multi-cluster warehouse is not force-suspended.
    row = {
        "name": "WH1", "state": "STARTED", "type": "STANDARD",
        "started_clusters": 2, "min_cluster_count": 1, "max_cluster_count": 4,
        "running": 0, "queued": 0, "auto_suspend": 60, "auto_resume": "true",
        "resumed_on": NOW - timedelta(seconds=90), "created_on": NOW - timedelta(days=5),
    }
    [wh] = parse_warehouses([row], now=NOW)
    assert should_force_suspend(
        wh, now=NOW, uptime_floor_seconds=62,
        in_cooldown=False, is_drifted=False, has_outstanding_intent=False,
    ) is False

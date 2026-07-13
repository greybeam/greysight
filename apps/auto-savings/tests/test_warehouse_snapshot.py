from datetime import datetime, timedelta, timezone

from auto_savings.warehouse_snapshot import parse_warehouses, uptime_seconds

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _row(**overrides):
    base = {
        "name": "WH1", "state": "STARTED", "type": "STANDARD", "size": "X-Small",
        "started_clusters": 1, "min_cluster_count": 1, "max_cluster_count": 1,
        "running": 0, "queued": 0, "auto_suspend": 300, "auto_resume": "true",
        "resumed_on": NOW - timedelta(seconds=90), "created_on": NOW - timedelta(days=5),
    }
    base.update(overrides)
    return base


def test_parse_maps_columns_by_name():
    [wh] = parse_warehouses([_row()], now=NOW)
    assert wh.name == "WH1"
    assert wh.type == "STANDARD"
    assert wh.auto_resume is True
    assert wh.started_clusters == 1


def test_parse_tolerates_missing_columns_and_case():
    [wh] = parse_warehouses([{"NAME": "WH2", "state": "SUSPENDED", "type": "STANDARD"}], now=NOW)
    assert wh.name == "WH2"
    assert wh.running == 0  # missing → default
    assert wh.resumed_on is None


def test_uptime_from_tz_aware_resumed_on():
    [wh] = parse_warehouses([_row()], now=NOW)
    assert uptime_seconds(wh, now=NOW) == 90.0


def test_uptime_none_when_never_resumed():
    [wh] = parse_warehouses([_row(resumed_on=None)], now=NOW)
    assert uptime_seconds(wh, now=NOW) is None


def test_string_resumed_on_is_coerced_to_tz_aware():
    # SHOW WAREHOUSES often returns timestamps as strings — the parser must coerce,
    # not pass them through (finding #3). Use the exact format the Task 0 spike recorded.
    [wh] = parse_warehouses([_row(resumed_on="2026-07-12 11:58:30.000 -0000")], now=NOW)
    assert wh.resumed_on is not None
    assert wh.resumed_on.tzinfo is not None
    assert uptime_seconds(wh, now=NOW) == 90.0


def test_naive_resumed_on_is_assumed_utc():
    [wh] = parse_warehouses([_row(resumed_on=datetime(2026, 7, 12, 11, 58, 30))], now=NOW)
    assert wh.resumed_on.tzinfo is not None
    assert uptime_seconds(wh, now=NOW) == 90.0


def test_absent_cluster_columns_default_started_to_min_standard_edition():
    # Task 0 spike: SHOW WAREHOUSES omits started_clusters/min_cluster_count/
    # max_cluster_count entirely on Standard edition (Enterprise+-only columns). A
    # single-cluster Standard warehouse must still be recognized as suspend-eligible,
    # i.e. started_clusters must default to the SAME value as min_cluster_count (not 0).
    row = {
        "name": "WH1", "state": "STARTED", "type": "STANDARD",
        "running": 0, "queued": 0, "auto_suspend": 60, "auto_resume": "true",
        "resumed_on": NOW - timedelta(seconds=90), "created_on": NOW - timedelta(days=5),
    }
    [wh] = parse_warehouses([row], now=NOW)
    assert wh.started_clusters == wh.min_cluster_count
    assert wh.min_cluster_count == 1
    assert wh.started_clusters == 1


def test_present_started_clusters_is_not_overridden_by_min_default():
    # Enterprise+ multi-cluster warehouse: the real started_clusters must be kept even
    # though min_cluster_count differs, so scaled-up warehouses remain protected.
    [wh] = parse_warehouses([_row(started_clusters=3, min_cluster_count=1, max_cluster_count=4)], now=NOW)
    assert wh.started_clusters == 3
    assert wh.min_cluster_count == 1

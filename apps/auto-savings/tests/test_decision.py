from datetime import datetime, timedelta, timezone

import pytest

from auto_savings import decision
from auto_savings.warehouse_snapshot import WarehouseSnapshot, parse_warehouses

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
CREATED_ON = NOW - timedelta(days=5)


def _wh(*, include_quiescing: bool = True, **overrides) -> WarehouseSnapshot:
    base = dict(
        name="WH1",
        state="STARTED",
        type="STANDARD",
        size="X-Small",
        started_clusters=1,
        min_cluster_count=1,
        max_cluster_count=1,
        running=0,
        queued=0,
        quiescing=0,
        quiescing_valid=True,
        auto_suspend=300,
        auto_resume=True,
        resumed_on=NOW - timedelta(seconds=90),
        created_on=CREATED_ON,
    )
    base.update(overrides)
    if not include_quiescing:
        base.pop("quiescing")
        base.pop("quiescing_valid")
    return WarehouseSnapshot(**base)


def _decide(warehouse: WarehouseSnapshot, **overrides) -> bool:
    arguments = dict(
        now=NOW,
        uptime_floor_seconds=62,
        enrolled_created_on=CREATED_ON,
    )
    arguments.update(overrides)
    return decision.should_suspend(warehouse, **arguments)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"type": "SNOWPARK-OPTIMIZED"}, False),
        ({"state": "SUSPENDING"}, False),
        ({"running": 1}, False),
        ({"queued": 1}, False),
        ({"quiescing": 1}, False),
        ({"auto_resume": False}, False),
        ({"resumed_on": None}, False),
        ({"resumed_on": NOW - timedelta(seconds=61)}, False),
    ],
)
def test_direct_suspend_truth_table(overrides, expected):
    assert _decide(_wh(**overrides)) is expected


def test_uptime_floor_is_inclusive_at_exact_boundary():
    almost_at_floor = NOW - timedelta(seconds=61, microseconds=999999)

    assert _decide(_wh(resumed_on=almost_at_floor)) is False
    assert _decide(_wh(resumed_on=NOW - timedelta(seconds=62))) is True


@pytest.mark.parametrize(
    "cluster_values",
    [
        {"started_clusters": 3, "min_cluster_count": 1, "max_cluster_count": 4},
        {
            "started_clusters": None,
            "min_cluster_count": None,
            "max_cluster_count": None,
        },
    ],
)
def test_multi_cluster_and_unknown_counts_are_eligible(cluster_values):
    assert _decide(_wh(**cluster_values)) is True


@pytest.mark.parametrize("auto_suspend", [None, 0, 1, 30])
def test_auto_suspend_is_audit_only(auto_suspend):
    assert _decide(_wh(auto_suspend=auto_suspend)) is True


@pytest.mark.parametrize(
    "validity_override",
    [
        {"activity_valid": False},
        {"quiescing_valid": False},
    ],
)
def test_invalid_activity_fields_fail_closed(validity_override):
    assert _decide(_wh(**validity_override)) is False


def test_direct_snapshot_without_quiescing_validity_fails_closed():
    warehouse = _wh(include_quiescing=False)

    assert _decide(warehouse) is False


def test_created_on_must_match_enrollment_identity():
    assert _decide(_wh(created_on=None)) is False
    assert _decide(_wh(created_on=CREATED_ON - timedelta(seconds=1))) is False


@pytest.mark.parametrize("field", ["resumed_on", "created_on"])
def test_malformed_decision_timestamp_fails_closed_without_parser_error(field):
    row = {
        "name": "WH1",
        "state": "STARTED",
        "type": "STANDARD",
        "running": 0,
        "queued": 0,
        "quiescing": 0,
        "auto_resume": "true",
        "resumed_on": NOW - timedelta(seconds=90),
        "created_on": CREATED_ON,
    }
    row[field] = "not-a-timestamp"

    [snapshot] = parse_warehouses([row], now=NOW)

    assert _decide(snapshot) is False

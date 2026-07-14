from datetime import datetime, timedelta, timezone

import pytest

from auto_savings.decision import should_suspend
from auto_savings.warehouse_snapshot import parse_warehouses, uptime_seconds

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _row(**overrides):
    base = {
        "name": "WH1",
        "state": "STARTED",
        "type": "STANDARD",
        "size": "X-Small",
        "started_clusters": 1,
        "min_cluster_count": 1,
        "max_cluster_count": 1,
        "running": 0,
        "queued": 0,
        "quiescing": 0,
        "auto_suspend": 300,
        "auto_resume": "true",
        "resumed_on": NOW - timedelta(seconds=90),
        "created_on": NOW - timedelta(days=5),
    }
    base.update(overrides)
    return base


def test_parse_tolerates_missing_columns_and_case():
    [warehouse] = parse_warehouses(
        [{"NAME": "WH2", "state": "SUSPENDED", "type": "STANDARD"}],
        now=NOW,
    )

    assert warehouse.name == "WH2"
    assert warehouse.activity_valid is False
    assert warehouse.quiescing_valid is False
    assert warehouse.resumed_on is None


def test_uptime_none_when_never_resumed():
    [warehouse] = parse_warehouses([_row(resumed_on=None)], now=NOW)

    assert uptime_seconds(warehouse, now=NOW) is None


def test_string_resumed_on_is_coerced_to_tz_aware():
    [warehouse] = parse_warehouses(
        [_row(resumed_on="2026-07-12 11:58:30.000 -0000")],
        now=NOW,
    )

    assert warehouse.resumed_on is not None
    assert warehouse.resumed_on.tzinfo is not None
    assert uptime_seconds(warehouse, now=NOW) == 90.0


def test_naive_datetime_fails_closed():
    [warehouse] = parse_warehouses(
        [_row(resumed_on=datetime(2026, 7, 12, 11, 58, 30))],
        now=NOW,
    )

    assert warehouse.resumed_on is None


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-12",
        "2026-07-12 11:58:30.000",
        "2026-07-12T11:58:30",
    ],
)
def test_timezone_less_timestamp_string_fails_closed(value):
    [warehouse] = parse_warehouses([_row(created_on=value)], now=NOW)

    assert warehouse.created_on is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("started_clusters", None),
        ("started_clusters", ""),
        ("min_cluster_count", "unknown"),
        ("min_cluster_count", ""),
        ("max_cluster_count", -1),
        ("max_cluster_count", ""),
    ],
)
def test_missing_or_malformed_cluster_counts_are_nullable_audit_fields(field, value):
    row = _row()
    if value is None:
        row.pop(field)
    else:
        row[field] = value

    [warehouse] = parse_warehouses([row], now=NOW)

    assert getattr(warehouse, field) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("running", None),
        ("running", "unknown"),
        ("running", ""),
        ("queued", None),
        ("queued", "unknown"),
        ("queued", ""),
    ],
)
def test_missing_or_malformed_activity_is_invalid(field, value):
    row = _row()
    if value is None:
        row.pop(field)
    else:
        row[field] = value

    [warehouse] = parse_warehouses([row], now=NOW)

    assert warehouse.activity_valid is False


@pytest.mark.parametrize(
    "value",
    [None, " ", "unknown", -1, 0.5, "0.5", float("nan"), float("inf"), True],
)
def test_quiescing_accepts_only_present_finite_nonnegative_integers(value):
    row = _row()
    if value is None:
        row.pop("quiescing")
    else:
        row["quiescing"] = value

    [warehouse] = parse_warehouses([row], now=NOW)

    assert warehouse.quiescing_valid is False


@pytest.mark.parametrize("value", [0, 0.0, "0", "0.0", 2])
def test_quiescing_accepts_finite_exact_nonnegative_integers(value):
    [warehouse] = parse_warehouses([_row(quiescing=value)], now=NOW)

    assert warehouse.quiescing_valid is True


def test_empty_quiescing_from_show_is_valid_idle_and_suspend_eligible():
    [warehouse] = parse_warehouses([_row(quiescing="")], now=NOW)

    assert warehouse.quiescing == 0
    assert warehouse.quiescing_valid is True
    assert should_suspend(
        warehouse,
        now=NOW,
        uptime_floor_seconds=62,
        enrolled_created_on=warehouse.created_on,
    )


def test_explicit_null_quiescing_fails_closed():
    [warehouse] = parse_warehouses([_row(quiescing=None)], now=NOW)

    assert warehouse.quiescing_valid is False


@pytest.mark.parametrize(
    "value",
    ["", "unknown", -1, True, 0.5, "0.5", float("nan"), float("inf")],
)
def test_malformed_auto_suspend_is_none(value):
    [warehouse] = parse_warehouses([_row(auto_suspend=value)], now=NOW)

    assert warehouse.auto_suspend is None


@pytest.mark.parametrize("value", [0, 1, "30", "30.0"])
def test_auto_suspend_accepts_finite_exact_nonnegative_integers(value):
    [warehouse] = parse_warehouses([_row(auto_suspend=value)], now=NOW)

    assert warehouse.auto_suspend == int(float(value))

from types import SimpleNamespace

import pytest

from app.services import warehouse_directory


def _live(**overrides):
    row = {
        "name": "WH1",
        "type": "STANDARD",
        "state": "STARTED",
        "auto_resume": "true",
        "auto_suspend": "300",
        "quiescing": "0",
        "min_cluster_count": 1,
        "max_cluster_count": 1,
        "started_clusters": 1,
        "size": "X-Small",
    }
    row.update(overrides)
    return [row]


@pytest.mark.parametrize(
    ("privilege", "expected"), [("manage warehouses", True), ("USAGE", False)]
)
def test_manage_warehouses_grant_detection(monkeypatch, privilege, expected):
    monkeypatch.setattr(
        warehouse_directory,
        "execute_metadata_query",
        lambda sql, config=None: [{"privilege": privilege}],
    )
    assert (
        warehouse_directory.check_manage_warehouses_grant(
            config=object(), role_name="RL"
        )
        is expected
    )


def test_grant_role_identifier_is_escaped(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        warehouse_directory,
        "execute_metadata_query",
        lambda sql, config=None: seen.setdefault("sql", sql) or [],
    )
    warehouse_directory.check_manage_warehouses_grant(
        config=object(), role_name='weird"role'
    )
    assert '"weird""role"' in seen["sql"]


def test_join_returns_final_response_shape_with_live_values():
    enrollment = SimpleNamespace(warehouse_name="WH1", enabled=True)
    [view] = warehouse_directory.join_warehouse_view(_live(), [enrollment])

    assert vars(view) == {
        "name": "WH1",
        "size": "X-Small",
        "state": "STARTED",
        "type": "STANDARD",
        "supported": True,
        "min_cluster_count": 1,
        "max_cluster_count": 1,
        "started_clusters": 1,
        "auto_resume_ok": True,
        "auto_suspend": 300,
        "quiescing": 0,
        "enabled": True,
        "status": "idle",
    }


def test_join_matches_case_distinct_warehouse_names_exactly():
    enrollments = [SimpleNamespace(warehouse_name="casewh", enabled=True)]
    views = warehouse_directory.join_warehouse_view(
        _live(name="CASEWH") + _live(name="casewh"), enrollments
    )

    assert [(view.name, view.enabled) for view in views] == [
        ("CASEWH", False),
        ("casewh", True),
    ]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"type": "SNOWPARK-OPTIMIZED"}, "unsupported"),
        ({"state": "SUSPENDING"}, "transitioning"),
        ({"state": "RESUMING"}, "transitioning"),
        ({"quiescing": 1}, "transitioning"),
        ({}, "idle"),
    ],
)
def test_join_statuses(overrides, expected):
    [view] = warehouse_directory.join_warehouse_view(_live(**overrides), [])
    assert view.status == expected


def test_join_maps_empty_show_quiescing_to_idle_zero():
    [view] = warehouse_directory.join_warehouse_view(_live(quiescing=""), [])

    assert view.quiescing == 0
    assert view.status == "idle"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("auto_suspend", ""),
        ("auto_suspend", "not-an-int"),
        ("auto_suspend", -1),
        ("quiescing", "not-an-int"),
        ("quiescing", -1),
    ],
)
def test_join_treats_malformed_nonnegative_counts_as_unknown(field, value):
    [view] = warehouse_directory.join_warehouse_view(_live(**{field: value}), [])
    assert getattr(view, field) is None
    assert view.status == "idle"


@pytest.mark.parametrize(
    "value",
    [None, " ", "not-an-int", -1, True, 0.5, "0.5", float("nan"), float("inf")],
)
def test_join_rejects_nonempty_invalid_quiescing_encodings(value):
    [view] = warehouse_directory.join_warehouse_view(_live(quiescing=value), [])

    assert view.quiescing is None
    assert view.status == "idle"


def test_join_defaults_absent_cluster_columns_to_single_cluster():
    [view] = warehouse_directory.join_warehouse_view(
        _live(min_cluster_count=None, started_clusters=None, max_cluster_count=None),
        [],
    )
    assert view.min_cluster_count == 1
    assert view.started_clusters == 1
    assert view.max_cluster_count is None

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import warehouse_directory


def _enrollment(name, **overrides):
    base = dict(
        warehouse_name=name,
        enabled=True,
        managed_auto_suspend=60,
        stored_default_auto_suspend=60,
        drift_state="ok",
        drifted_value=None,
        cooldown_ts=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _standard_live(name="WH1", state="STARTED"):
    return [{"name": name, "type": "STANDARD", "state": state, "auto_resume": "true",
             "auto_suspend": 60, "min_cluster_count": 1, "max_cluster_count": 1,
             "started_clusters": 1, "size": "X-Small"}]


def test_status_in_cooldown_while_cooldown_is_in_the_future():
    now = datetime(2026, 7, 13, 21, 47, 0, tzinfo=timezone.utc)
    enrollment = _enrollment("WH1", cooldown_ts=(now + timedelta(seconds=30)).isoformat())
    [view] = warehouse_directory.join_warehouse_view(_standard_live(), [enrollment], now=now)
    assert view.status == "in_cooldown"


def test_status_reverts_to_idle_once_cooldown_has_expired():
    # The worker writes cooldown_ts on restore and never nulls it; the status must
    # expire it by comparing against now, not treat any non-null value as active.
    now = datetime(2026, 7, 13, 21, 49, 0, tzinfo=timezone.utc)
    enrollment = _enrollment("WH1", cooldown_ts=(now - timedelta(seconds=60)).isoformat())
    [view] = warehouse_directory.join_warehouse_view(_standard_live(), [enrollment], now=now)
    assert view.status == "idle"


def test_grant_present_detected(monkeypatch):
    monkeypatch.setattr(warehouse_directory, "execute_metadata_query",
                        lambda sql, config=None: [{"privilege": "manage warehouses"}])  # case-insensitive
    assert warehouse_directory.check_manage_warehouses_grant(config=object(), role_name="RL") is True


def test_grant_absent(monkeypatch):
    monkeypatch.setattr(warehouse_directory, "execute_metadata_query",
                        lambda sql, config=None: [{"privilege": "USAGE"}])
    assert warehouse_directory.check_manage_warehouses_grant(config=object(), role_name="RL") is False


def test_grant_role_identifier_is_escaped(monkeypatch):
    seen = {}
    monkeypatch.setattr(warehouse_directory, "execute_metadata_query",
                        lambda sql, config=None: seen.setdefault("sql", sql) or [])
    warehouse_directory.check_manage_warehouses_grant(config=object(), role_name='weird"role')
    assert '"weird""role"' in seen["sql"]  # embedded quote doubled, not injected


def test_join_marks_non_standard_unsupported():
    live = [{"name": "SP1", "type": "SNOWPARK-OPTIMIZED", "state": "STARTED",
             "auto_resume": "true", "auto_suspend": 300, "min_cluster_count": 1,
             "max_cluster_count": 1, "started_clusters": 1, "size": "MEDIUM"}]
    [view] = warehouse_directory.join_warehouse_view(live, [])
    assert view.supported is False


def test_join_defaults_absent_cluster_columns_to_single_cluster():
    # Standard edition omits min_cluster_count/started_clusters entirely (Task 0 spike);
    # the UI's "# clusters" column should show 1, not blank/None.
    live = [{"name": "WH1", "type": "STANDARD", "state": "STARTED",
             "auto_resume": "true", "auto_suspend": 300, "size": "X-Small"}]
    [view] = warehouse_directory.join_warehouse_view(live, [])
    assert view.min_cluster_count == 1
    assert view.started_clusters == 1
    assert view.max_cluster_count is None  # not used by the gate; left as-is


def test_join_keeps_real_cluster_values_when_present():
    live = [{"name": "WH2", "type": "STANDARD", "state": "STARTED",
             "auto_resume": "true", "auto_suspend": 300, "min_cluster_count": 1,
             "max_cluster_count": 4, "started_clusters": 3, "size": "MEDIUM"}]
    [view] = warehouse_directory.join_warehouse_view(live, [])
    assert view.min_cluster_count == 1
    assert view.started_clusters == 3
    assert view.max_cluster_count == 4

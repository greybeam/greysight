from app.services import warehouse_directory


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

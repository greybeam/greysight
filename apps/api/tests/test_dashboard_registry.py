from __future__ import annotations

import re
from pathlib import Path

import pytest

import app.services.dashboard_registry as dashboard_registry_module
from app.services.dashboard_registry import (
    DashboardRegistry,
    DashboardSource,
    DerivedDataset,
    load_dashboard_registry,
)


EXPECTED_SOURCE_SQL = {
    "warehouse_spend_daily": Path("sql/snowflake/warehouse_spend_daily.sql"),
    "service_spend_daily": Path("sql/snowflake/service_spend_daily.sql"),
    "query_compute_by_user_daily": Path(
        "sql/snowflake/query_compute_by_user_daily.sql"
    ),
    "database_storage_daily": Path("sql/snowflake/database_storage_daily.sql"),
    "org_spend_daily": Path("sql/snowflake/org_spend_daily.sql"),
    "rate_sheet_daily": Path("sql/snowflake/rate_sheet_daily.sql"),
    "current_account": Path("sql/snowflake/current_account.sql"),
}

ACCOUNT_USAGE_SOURCE_IDS = {
    "warehouse_spend_daily",
    "service_spend_daily",
    "query_compute_by_user_daily",
    "database_storage_daily",
}

FORBIDDEN_SQL = re.compile(r"\b(insert|update|delete|merge|drop)\b", re.IGNORECASE)


def test_load_dashboard_registry_returns_typed_registry() -> None:
    registry = load_dashboard_registry()

    assert isinstance(registry, DashboardRegistry)
    assert set(registry.sources) == set(EXPECTED_SOURCE_SQL)
    assert all(
        isinstance(source, DashboardSource) for source in registry.sources.values()
    )
    assert all(
        isinstance(dataset, DerivedDataset)
        for dataset in registry.derived_datasets.values()
    )


def test_account_spend_daily_is_derived_not_snowflake_source() -> None:
    registry = load_dashboard_registry()

    assert "account_spend_daily" not in registry.sources
    assert "account_spend_daily" in registry.derived_datasets
    assert registry.derived_datasets["account_spend_daily"].depends_on == (
        "service_spend_daily",
    )


def test_source_sql_paths_exist_under_sql() -> None:
    registry = load_dashboard_registry()

    for source_name, expected_path in EXPECTED_SOURCE_SQL.items():
        source = registry.sources[source_name]

        if source_name in ACCOUNT_USAGE_SOURCE_IDS:
            assert source.kind == "snowflake_account_usage"
        assert source.sql_path == expected_path
        assert source.resolved_sql_path.is_file()
        assert source.resolved_sql_path.is_relative_to(registry.root_path / "sql")


def test_source_sql_uses_required_account_usage_contract() -> None:
    registry = load_dashboard_registry()

    for source in registry.sources.values():
        if source.kind != "snowflake_account_usage":
            continue
        sql = source.sql
        normalized_sql = " ".join(sql.lower().split())

        assert "%(window_days)s" in sql
        assert "{window_days}" not in sql
        assert "snowflake.account_usage." in normalized_sql
        assert "convert_timezone('utc', current_timestamp())" in normalized_sql
        assert "current_date()" not in normalized_sql
        assert "dateadd(" in normalized_sql
        assert "-%(window_days)s" in normalized_sql
        assert "group by" in normalized_sql
        assert not FORBIDDEN_SQL.search(sql)


def test_query_compute_by_user_source_returns_attributed_compute_credits() -> None:
    registry = load_dashboard_registry()
    sql = registry.sources["query_compute_by_user_daily"].sql.lower()

    assert "query_attribution_history" in sql
    assert "credits_attributed_compute" in sql
    assert "query_history" not in sql
    assert " as credits_attributed_compute" in sql
    assert "query_count" not in sql
    assert "cloud_services_credits" not in sql


def test_query_compute_by_user_source_has_complete_filters() -> None:
    registry = load_dashboard_registry()
    sql = registry.sources["query_compute_by_user_daily"].sql.lower()

    assert "convert_timezone('utc', current_timestamp())::date" in sql
    assert "and convert_timezone('utc', start_time)::date <" in sql
    assert "and warehouse_name is not null" in sql
    assert "and user_name is not null" in sql


def test_registry_rejects_derived_dependencies_without_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "dashboard_sources.yml"
    registry_path.write_text(
        """
sources:
  - id: service_spend_daily
    kind: snowflake_account_usage
    sql_path: sql/snowflake/service_spend_daily.sql
    grain:
      - usage_date
      - service_type
derived_datasets:
  - id: account_spend_daily
    depends_on:
      - missing_source
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_registry_module, "_REGISTRY_PATH", registry_path)

    with pytest.raises(ValueError, match="missing_source"):
        load_dashboard_registry()


def _load_registry_from_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    yaml_content: str,
) -> DashboardRegistry:
    registry_path = tmp_path / "dashboard_sources.yml"
    registry_path.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setattr(dashboard_registry_module, "_REGISTRY_PATH", registry_path)
    return load_dashboard_registry()


def test_registry_rejects_parent_directory_sql_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="sql_path"):
        _load_registry_from_yaml(
            tmp_path,
            monkeypatch,
            """
sources:
  - id: service_spend_daily
    kind: snowflake_account_usage
    sql_path: ../secrets.sql
    grain:
      - usage_date
derived_datasets: []
""",
        )


def test_registry_rejects_absolute_sql_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="sql_path"):
        _load_registry_from_yaml(
            tmp_path,
            monkeypatch,
            """
sources:
  - id: service_spend_daily
    kind: snowflake_account_usage
    sql_path: /tmp/service_spend_daily.sql
    grain:
      - usage_date
derived_datasets: []
""",
        )


def test_registry_rejects_duplicate_source_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="service_spend_daily"):
        _load_registry_from_yaml(
            tmp_path,
            monkeypatch,
            """
sources:
  - id: service_spend_daily
    kind: snowflake_account_usage
    sql_path: sql/snowflake/service_spend_daily.sql
    grain:
      - usage_date
  - id: service_spend_daily
    kind: snowflake_account_usage
    sql_path: sql/snowflake/warehouse_spend_daily.sql
    grain:
      - usage_date
derived_datasets: []
""",
        )


def test_registry_rejects_duplicate_derived_dataset_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="account_spend_daily"):
        _load_registry_from_yaml(
            tmp_path,
            monkeypatch,
            """
sources:
  - id: service_spend_daily
    kind: snowflake_account_usage
    sql_path: sql/snowflake/service_spend_daily.sql
    grain:
      - usage_date
derived_datasets:
  - id: account_spend_daily
    depends_on:
      - service_spend_daily
  - id: account_spend_daily
    depends_on:
      - service_spend_daily
""",
        )


def test_registry_rejects_direct_derived_dataset_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="cycle"):
        _load_registry_from_yaml(
            tmp_path,
            monkeypatch,
            """
sources:
  - id: service_spend_daily
    kind: snowflake_account_usage
    sql_path: sql/snowflake/service_spend_daily.sql
    grain:
      - usage_date
derived_datasets:
  - id: account_spend_daily
    depends_on:
      - account_spend_daily
""",
        )


def test_registry_rejects_transitive_derived_dataset_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="cycle"):
        _load_registry_from_yaml(
            tmp_path,
            monkeypatch,
            """
sources:
  - id: service_spend_daily
    kind: snowflake_account_usage
    sql_path: sql/snowflake/service_spend_daily.sql
    grain:
      - usage_date
derived_datasets:
  - id: account_spend_daily
    depends_on:
      - monthly_account_spend
  - id: monthly_account_spend
    depends_on:
      - account_spend_daily
""",
        )


def test_registry_rejects_unknown_source_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.dashboard_registry as registry_module

    registry_path = tmp_path / "sql" / "dashboard_sources.yml"
    sql_path = tmp_path / "sql" / "snowflake" / "bad.sql"
    sql_path.parent.mkdir(parents=True)
    sql_path.write_text("select 1", encoding="utf-8")
    registry_path.write_text(
        "sources:\n"
        "  - id: bad_source\n"
        "    kind: snowflake_anything\n"
        "    sql_path: sql/snowflake/bad.sql\n"
        "    grain:\n"
        "      - usage_date\n"
        "derived_datasets: []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_module, "_ROOT_PATH", tmp_path)
    monkeypatch.setattr(registry_module, "_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(registry_module, "_SQL_ROOT", tmp_path / "sql")

    with pytest.raises(ValueError, match="kind"):
        registry_module.load_dashboard_registry()


def test_registry_includes_organization_usage_and_metadata_sources() -> None:
    registry = load_dashboard_registry()

    org_spend = registry.sources["org_spend_daily"]
    assert org_spend.kind == "snowflake_organization_usage"
    assert "usage_in_currency_daily" in org_spend.sql.lower()
    assert "%(account_locator)s" in org_spend.sql

    rate_sheet = registry.sources["rate_sheet_daily"]
    assert rate_sheet.kind == "snowflake_organization_usage"
    assert "rate_sheet_daily" in rate_sheet.sql.lower()
    assert "%(account_locator)s" in rate_sheet.sql

    current_account = registry.sources["current_account"]
    assert current_account.kind == "snowflake_metadata"
    assert "current_account()" in current_account.sql.lower()

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase/migrations/202606080001_initial_cost_dashboard.sql"
)

REQUIRED_TABLES = [
    "organizations",
    "organization_memberships",
    "snowflake_connections",
    "connection_validation_results",
    "analysis_runs",
    "analysis_run_datasets",
    "audit_events",
    "dashboard_filter_preferences",
]


def read_migration_sql() -> str:
    return MIGRATION.read_text().lower()


def test_migration_enables_rls_for_public_tables() -> None:
    sql = read_migration_sql()

    for table in REQUIRED_TABLES:
        assert f"alter table {table} enable row level security" in sql


def test_migration_indexes_org_and_run_access_patterns() -> None:
    sql = read_migration_sql()

    assert "analysis_runs(organization_id, created_at desc)" in sql
    assert "analysis_run_datasets(run_id, dataset_key)" in sql
    assert "credential_reference" in sql
    assert "private_key" not in sql


def test_migration_defines_member_and_admin_guards() -> None:
    sql = read_migration_sql()

    assert "create or replace function is_organization_member" in sql
    assert "create or replace function is_organization_admin" in sql
    assert "role in ('owner', 'admin')" in sql
    assert (
        "grant execute on function is_organization_member(uuid) to authenticated" in sql
    )
    assert (
        "grant execute on function is_organization_admin(uuid) to authenticated" in sql
    )


def test_membership_mutations_require_admin_role() -> None:
    sql = read_migration_sql()

    assert "organization_memberships_insert_for_admins" in sql
    assert "organization_memberships_update_for_admins" in sql
    assert "organization_memberships_delete_for_admins" in sql
    assert "organization_memberships_insert_for_members" not in sql
    assert "organization_memberships_update_for_members" not in sql
    assert "organization_memberships_delete_for_members" not in sql


def test_organization_insert_creates_owner_membership() -> None:
    sql = read_migration_sql()

    assert "created_by_user_id uuid references auth.users(id)" in sql
    assert "create_organization_owner_membership" in sql
    assert "organizations_create_owner_membership" in sql
    assert "values (new.id, new.created_by_user_id, 'owner')" in sql
    assert "created_by_user_id = auth.uid()" in sql


def test_sensitive_org_state_writes_require_admin_role() -> None:
    sql = read_migration_sql()

    for table in [
        "snowflake_connections",
        "connection_validation_results",
        "analysis_runs",
        "analysis_run_datasets",
        "audit_events",
    ]:
        assert f"{table}_insert_for_admins" in sql
        assert f"{table}_insert_for_members" not in sql


def test_dashboard_preferences_are_limited_to_current_user() -> None:
    sql = read_migration_sql()

    assert "dashboard_filter_preferences_select_for_owner" in sql
    assert "dashboard_filter_preferences_insert_for_owner" in sql
    assert "user_id = auth.uid()" in sql

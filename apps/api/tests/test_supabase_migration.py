from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase/migrations"


def _migration_path() -> Path:
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert migrations, f"no migration files found in {MIGRATIONS_DIR}"
    assert len(migrations) == 1, (
        f"expected a single migration file, found: {[m.name for m in migrations]}"
    )
    return migrations[0]


REQUIRED_TABLES = [
    "organizations",
    "organization_memberships",
]

DROPPED_TABLES = [
    "snowflake_connections",
    "connection_validation_results",
    "analysis_runs",
    "analysis_run_datasets",
    "audit_events",
    "dashboard_filter_preferences",
]


def read_migration_sql() -> str:
    return _migration_path().read_text().lower()


def test_migration_defines_only_org_and_membership_tables() -> None:
    sql = read_migration_sql()

    for table in REQUIRED_TABLES:
        assert f"create table {table} (" in sql

    for table in DROPPED_TABLES:
        assert f"create table {table}" not in sql


def test_migration_enables_rls_for_public_tables() -> None:
    sql = read_migration_sql()

    for table in REQUIRED_TABLES:
        assert f"alter table {table} enable row level security" in sql


def test_membership_references_organizations_with_cascade() -> None:
    sql = read_migration_sql()

    assert (
        "organization_id uuid not null references organizations(id) on delete cascade"
        in sql
    )
    assert "user_id uuid not null references auth.users(id) on delete cascade" in sql
    assert "create index organization_memberships_user_id_idx" in sql


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

    assert "organization_memberships_select_for_members" in sql
    assert "organization_memberships_insert_for_admins" in sql
    assert "organization_memberships_update_for_admins" in sql
    assert "organization_memberships_delete_for_admins" in sql
    assert "organization_memberships_insert_for_members" not in sql
    assert "organization_memberships_update_for_members" not in sql
    assert "organization_memberships_delete_for_members" not in sql


def test_organization_policies_gate_on_membership() -> None:
    sql = read_migration_sql()

    assert "organizations_select_for_members" in sql
    assert "organizations_insert_for_authenticated" in sql
    assert "organizations_update_for_admins" in sql


def test_organization_insert_creates_owner_membership() -> None:
    sql = read_migration_sql()

    assert "created_by_user_id uuid references auth.users(id)" in sql
    assert "create or replace function create_organization_owner_membership" in sql
    assert "create trigger organizations_create_owner_membership" in sql
    assert "values (new.id, new.created_by_user_id, 'owner')" in sql
    assert "created_by_user_id = auth.uid()" in sql


def test_migration_drops_legacy_cost_dashboard_schema() -> None:
    sql = read_migration_sql()

    for table in DROPPED_TABLES:
        assert f"{table}_insert_for_admins" not in sql
        assert f"{table}_select_for_owner" not in sql

    assert "analysis_runs(organization_id, created_at desc)" not in sql
    assert "analysis_run_datasets(run_id, dataset_key)" not in sql
    assert "credential_reference" not in sql
    assert "dashboard_filter_preferences_select_for_owner" not in sql

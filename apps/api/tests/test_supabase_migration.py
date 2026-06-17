from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase/migrations"


def _migration_paths() -> list[Path]:
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert migrations, f"no migration files found in {MIGRATIONS_DIR}"
    return migrations


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
    return "\n".join(path.read_text() for path in _migration_paths()).lower()


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
    # Org creation is service-role/RPC-only: no authenticated INSERT policy.
    assert "organizations_insert_for_authenticated" not in sql
    assert "organizations_update_for_admins" in sql


def test_organization_insert_creates_owner_membership() -> None:
    sql = read_migration_sql()

    assert "created_by_user_id uuid references auth.users(id)" in sql
    assert "create or replace function create_organization_owner_membership" in sql
    assert "create trigger organizations_create_owner_membership" in sql
    assert "values (new.id, new.created_by_user_id, 'owner')" in sql


def test_migration_drops_legacy_cost_dashboard_schema() -> None:
    sql = read_migration_sql()

    for table in DROPPED_TABLES:
        assert f"{table}_insert_for_admins" not in sql
        assert f"{table}_select_for_owner" not in sql

    assert "analysis_runs(organization_id, created_at desc)" not in sql
    assert "analysis_run_datasets(run_id, dataset_key)" not in sql
    assert "credential_reference" not in sql
    assert "dashboard_filter_preferences_select_for_owner" not in sql


def test_connection_table_defined_with_rls_and_no_authenticated_writes() -> None:
    sql = read_migration_sql()
    assert "create table organization_snowflake_connections (" in sql
    assert (
        "organization_id uuid primary key references organizations(id) on delete cascade"
        in sql
    )
    assert "secret_id uuid" in sql
    assert (
        "status text not null default 'invalid' check (status in ('active', 'invalid'))"
        in sql
    )
    assert (
        "alter table organization_snowflake_connections enable row level security"
        in sql
    )
    # members may read only via the summary function; no authenticated DML policies
    # policy-prefix form avoids colliding with the legitimate _delete_secret trigger (Task 2)
    assert "create policy organization_snowflake_connections_insert" not in sql
    assert "create policy organization_snowflake_connections_update" not in sql
    assert "create policy organization_snowflake_connections_delete" not in sql
    # members can read non-sensitive metadata through a SECURITY DEFINER function
    assert "create or replace function get_org_connection_summary" in sql
    assert (
        "secret_id"
        not in sql.split("get_org_connection_summary", 1)[1].split("$$", 2)[1]
    )


def test_secret_rpcs_are_service_role_only() -> None:
    sql = read_migration_sql()
    for fn in (
        "set_organization_snowflake_secret",
        "get_organization_snowflake_secret",
        "delete_organization_snowflake_secret",
    ):
        assert f"create or replace function {fn}" in sql
        assert f"revoke all on function {fn}" in sql
        assert f"grant execute on function {fn}" in sql
        # never granted to authenticated/anon — service_role only
        block = sql.split(f"grant execute on function {fn}", 1)[1].split(";", 1)[0]
        assert "to service_role" in block
        assert "authenticated" not in block
    assert "vault.create_secret" in sql
    assert "vault.update_secret" in sql
    assert "vault.decrypted_secrets" in sql


def test_vault_extension_enabled_and_teardown_trigger_present() -> None:
    sql = read_migration_sql()
    # Vault must be enabled by the migration, not assumed pre-installed.
    assert "create extension if not exists supabase_vault" in sql
    # Deleting/cascading a connection row must delete its Vault secret.
    assert "before delete on organization_snowflake_connections" in sql
    assert "delete from vault.secrets where id = old.secret_id" in sql
    # Disconnect must atomically clear the secret AND invalidate the row.
    assert "create or replace function disconnect_organization_snowflake" in sql
    block = sql.split(
        "create or replace function disconnect_organization_snowflake", 1
    )[1]
    assert "set secret_id = null, status = 'invalid'" in block
    grant = sql.split("grant execute on function disconnect_organization_snowflake", 1)[
        1
    ].split(";", 1)[0]
    assert "to service_role" in grant
    assert "authenticated" not in grant


def test_secret_lifecycle_hardening() -> None:
    sql = read_migration_sql()
    # delete RPC delegates to the atomic disconnect (no stale active+dead-secret row)
    delete_block = sql.split(
        "create or replace function delete_organization_snowflake_secret", 1
    )[1].split("$$", 2)[1]
    assert (
        "perform disconnect_organization_snowflake(target_organization_id)"
        in delete_block
    )
    # disconnect's UPDATE is guarded so repeated no-op disconnects don't churn updated_at
    assert "and (secret_id is not null or status <> 'invalid')" in sql


def test_atomic_create_rpc_and_one_org_guard() -> None:
    sql = read_migration_sql()
    assert "create or replace function create_org_with_snowflake_connection" in sql
    # race-safe: advisory lock keyed on the user id, inside the txn
    assert "pg_advisory_xact_lock" in sql
    # v1 one-org guard enforced in the DB, not the app
    assert "create unique index one_owner_membership_per_user" in sql
    assert "where role = 'owner'" in sql
    # service-role only
    block = sql.split(
        "grant execute on function create_org_with_snowflake_connection", 1
    )[1].split(";", 1)[0]
    assert "to service_role" in block
    assert "authenticated" not in block

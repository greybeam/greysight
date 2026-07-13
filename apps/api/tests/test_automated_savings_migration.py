from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase" / "migrations" / "202607120001_automated_savings.sql"
).read_text()


def test_tables_created():
    for table in (
        "automated_savings_settings",
        "automated_savings_warehouses",
        "automated_savings_restore_intents",
    ):
        assert f"create table {table}" in MIGRATION


def test_enabled_and_global_default_false():
    # Nothing is automated at opt-in.
    assert "global_enabled boolean not null default false" in MIGRATION
    assert "enabled boolean not null default false" in MIGRATION


def test_rls_members_read_admins_mutate():
    assert "enable row level security" in MIGRATION
    assert "is_organization_member" in MIGRATION
    assert "is_organization_admin" in MIGRATION


def test_one_restore_intent_per_warehouse():
    assert "primary key (organization_id, warehouse_name)" in MIGRATION


def test_drift_state_constraint():
    assert "check (drift_state in ('ok','drifted','unsupported'))" in MIGRATION


def test_managed_default_floor_and_stored_default_constraints():
    assert "managed_auto_suspend >= 60" in MIGRATION
    assert "stored_default_auto_suspend not in (0, 1)" in MIGRATION


def test_worker_tenants_includes_outstanding_intents():
    # A kill-switched org with an outstanding sentinel must still be enumerated so it drains.
    assert "automated_savings_restore_intents" in MIGRATION.split(
        "function automated_savings_worker_tenants"
    )[1]

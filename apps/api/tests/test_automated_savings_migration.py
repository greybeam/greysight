from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"

MIGRATION = (MIGRATIONS_DIR / "202607120001_automated_savings.sql").read_text()

SENTINEL_CONFIRMED_MIGRATION = (
    MIGRATIONS_DIR / "20260713223505_automated_savings_sentinel_confirmed.sql"
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


def test_restore_intent_has_baseline_resumed_on_column():
    # Resume-aware restore: baseline resumed_on captured at set-time so reconcile
    # can detect a completed suspend→resume cycle under the sentinel.
    assert "baseline_resumed_on timestamptz" in MIGRATION


def test_sentinel_confirmed_additive_migration_adds_non_null_default_false_column():
    # Prevent the stale-SHOW race: an unconfirmed sentinel must not be read as an
    # idempotently-completed restore. A durable, non-null default-false flag lets
    # reconcile HOLD until it observes AUTO_SUSPEND=1 and confirms ownership. Added
    # as a separate additive migration because 202607120001 is already applied.
    assert (
        "alter table automated_savings_restore_intents\n"
        "    add column if not exists sentinel_confirmed boolean not null default false;"
    ) in SENTINEL_CONFIRMED_MIGRATION


def test_restore_intent_has_cycle_id_for_event_pairing():
    # A set_sentinel event and its restore event share the intent's cycle_id.
    assert "cycle_id uuid not null default gen_random_uuid()" in MIGRATION


def test_restore_intent_kind_discriminates_sentinel_from_reapply():
    # A reapply intent (admin "re-apply old default") must be distinguishable so
    # the worker overwrites the drifted value instead of re-flagging drift.
    assert "kind text not null default 'sentinel' check (kind in ('sentinel', 'reapply'))" in MIGRATION


def test_events_audit_table_created_append_only():
    assert "create table automated_savings_events" in MIGRATION
    # Constrained action/reason (no free text), and a member-read-only policy —
    # the log is written solely by the worker (service role).
    assert "action in ('set_sentinel', 'restore')" in MIGRATION
    assert "reason in ('decide', 'suspended', 'busy', 'resume_aware', 'aged_out', 'reconcile_reapply')" in MIGRATION
    assert (
        "create policy automated_savings_events_read on automated_savings_events\n"
        "    for select to authenticated using (is_organization_member(organization_id));"
    ) in MIGRATION
    # No authenticated write policy on the audit table (append-only, worker-only).
    assert "on automated_savings_events\n    for all" not in MIGRATION


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


def test_upsert_enrollment_function_created_and_locked_down():
    assert (
        "create or replace function automated_savings_upsert_enrollment("
        in MIGRATION
    )
    assert "security definer" in MIGRATION.split(
        "function automated_savings_upsert_enrollment"
    )[1]
    assert (
        "revoke all on function automated_savings_upsert_enrollment(\n"
        "    uuid, text, boolean, integer, integer, timestamptz\n"
        ") from public;"
    ) in MIGRATION
    assert (
        "grant execute on function automated_savings_upsert_enrollment(\n"
        "    uuid, text, boolean, integer, integer, timestamptz\n"
        ") to service_role;"
    ) in MIGRATION


def test_upsert_enrollment_is_atomic_single_statement_insert_on_conflict():
    # Finding #5: the INSERT branch must always carry the freshly-captured
    # defaults, so a concurrent delete-then-upsert race can never produce a
    # null-default row.
    body = MIGRATION.split("function automated_savings_upsert_enrollment")[1]
    assert "insert into automated_savings_warehouses" in body
    assert "on conflict (organization_id, warehouse_name) do update" in body
    assert "p_stored_default, p_managed_default, p_warehouse_created_on" in body


def test_upsert_enrollment_preserves_default_only_when_created_on_matches():
    # Finding #11: preserve the existing stored_default/managed_auto_suspend
    # only when the stored warehouse_created_on matches the freshly-captured
    # live created_on (same physical warehouse) — otherwise (mismatch, or the
    # stored created_on is null/unknown) treat it as a fresh capture.
    body = MIGRATION.split("function automated_savings_upsert_enrollment")[1]
    assert (
        "when automated_savings_warehouses.warehouse_created_on is not null\n"
        "                 and automated_savings_warehouses.warehouse_created_on "
        "= excluded.warehouse_created_on\n"
        "            then automated_savings_warehouses.stored_default_auto_suspend\n"
        "            else excluded.stored_default_auto_suspend"
    ) in body
    assert (
        "when automated_savings_warehouses.warehouse_created_on is not null\n"
        "                 and automated_savings_warehouses.warehouse_created_on "
        "= excluded.warehouse_created_on\n"
        "            then automated_savings_warehouses.managed_auto_suspend\n"
        "            else excluded.managed_auto_suspend"
    ) in body

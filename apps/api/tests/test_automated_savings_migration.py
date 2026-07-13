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


def test_restore_intent_has_baseline_resumed_on_column():
    # Resume-aware restore: baseline resumed_on captured at set-time so reconcile
    # can detect a completed suspend→resume cycle under the sentinel.
    assert "baseline_resumed_on timestamptz" in MIGRATION


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

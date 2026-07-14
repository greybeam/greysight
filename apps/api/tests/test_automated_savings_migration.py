import re
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
MIGRATION = (MIGRATIONS_DIR / "202607120001_automated_savings.sql").read_text()


def _function_body(name: str) -> str:
    definition = re.search(
        rf"create (?:or replace )?function {re.escape(name)}\s*"
        r"\([^)]*\).*?\bas \$\$.*?\$\$;",
        MIGRATION,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert definition is not None, f"missing function definition: {name}"
    return definition.group(0)


def test_direct_schema_has_only_final_tables():
    assert "create table automated_savings_settings" in MIGRATION
    assert "create table automated_savings_warehouses" in MIGRATION
    assert "create table automated_savings_events" in MIGRATION
    assert "automated_savings_restore_intents" not in MIGRATION


def test_direct_enrollment_has_mandatory_identity_without_legacy_columns():
    assert "warehouse_created_on timestamptz not null" in MIGRATION
    for legacy in (
        "managed_auto_suspend",
        "stored_default_auto_suspend",
        "drift_state",
        "drifted_value",
        "sentinel_confirmed",
        "cooldown_ts",
    ):
        assert legacy not in MIGRATION


def test_direct_event_shape_is_suspend_only():
    assert "action text not null check (action = 'suspend')" in MIGRATION
    assert "reason text not null check (reason = 'idle')" in MIGRATION
    assert "observed_started_clusters integer" in MIGRATION
    assert "observed_min_cluster_count integer" in MIGRATION
    assert "observed_max_cluster_count integer" in MIGRATION
    assert (
        "observed_quiescing integer not null check (observed_quiescing >= 0)"
        in MIGRATION
    )
    assert "observed_state text not null" in MIGRATION
    assert (
        "observed_running integer not null check (observed_running >= 0)" in MIGRATION
    )
    assert "observed_queued integer not null check (observed_queued >= 0)" in MIGRATION
    assert "observed_resumed_on timestamptz not null" in MIGRATION
    for legacy in ("cycle_id", "from_value", "to_value", "set_sentinel", "restore"):
        assert legacy not in MIGRATION


def test_direct_schema_has_guarded_worker_rpcs():
    for function in (
        "automated_savings_upsert_enrollment",
        "automated_savings_disable_enrollment",
        "automated_savings_authorize_suspend",
        "automated_savings_delete_stale_enrollment",
        "automated_savings_worker_tenants",
    ):
        body = _function_body(function)
        assert "security invoker" in body
        assert "set search_path = ''" in body
        assert "public.automated_savings_" in body
    assert "s.global_enabled" in _function_body("automated_savings_authorize_suspend")


def test_function_parser_does_not_include_later_rpc_definitions():
    upsert = _function_body("automated_savings_upsert_enrollment")
    assert "automated_savings_authorize_suspend" not in upsert
    authorize = _function_body("automated_savings_authorize_suspend")
    assert "automated_savings_delete_stale_enrollment" not in authorize


def test_worker_rpcs_are_granted_only_to_service_role():
    signatures = (
        "automated_savings_upsert_enrollment(\n    uuid, text, boolean, timestamptz\n)",
        "automated_savings_disable_enrollment(\n    uuid, text\n)",
        "automated_savings_authorize_suspend(\n"
        "    uuid, text, timestamptz, timestamptz\n)",
        "automated_savings_delete_stale_enrollment(\n"
        "    uuid, text, timestamptz, timestamptz\n)",
        "automated_savings_worker_tenants()",
    )
    for signature in signatures:
        for role in ("public", "anon", "authenticated"):
            assert (
                f"revoke execute on function public.{signature} from {role};"
                in MIGRATION
            )
        assert (
            f"grant execute on function public.{signature} to service_role;"
            in MIGRATION
        )

    execute_grants = {
        (name, re.sub(r"\s+", " ", arguments).strip(), grantee.lower())
        for name, arguments, grantee in re.findall(
            r"grant\s+execute\s+on\s+function\s+public\."
            r"(automated_savings_[a-z_]+)\s*\(([^)]*)\)\s+to\s+([a-z_]+)\s*;",
            MIGRATION,
            flags=re.IGNORECASE | re.DOTALL,
        )
    }
    assert execute_grants == {
        (
            "automated_savings_upsert_enrollment",
            "uuid, text, boolean, timestamptz",
            "service_role",
        ),
        (
            "automated_savings_authorize_suspend",
            "uuid, text, timestamptz, timestamptz",
            "service_role",
        ),
        (
            "automated_savings_disable_enrollment",
            "uuid, text",
            "service_role",
        ),
        (
            "automated_savings_delete_stale_enrollment",
            "uuid, text, timestamptz, timestamptz",
            "service_role",
        ),
        ("automated_savings_worker_tenants", "", "service_role"),
    }


def test_direct_schema_keeps_explicit_rls_and_version_trigger():
    for table in (
        "automated_savings_settings",
        "automated_savings_warehouses",
        "automated_savings_events",
    ):
        assert f"alter table {table} enable row level security" in MIGRATION
    assert "set_automated_savings_settings_updated_at" in MIGRATION
    assert "create trigger set_automated_savings_warehouses_updated_at" in MIGRATION
    assert "automated_savings_warehouses_read" in MIGRATION
    assert "automated_savings_warehouses_insert" in MIGRATION
    assert "automated_savings_warehouses_update" in MIGRATION
    assert "for delete to authenticated" not in MIGRATION
    assert "automated_savings_events_write" not in MIGRATION
    assert "for all to authenticated" not in MIGRATION


def test_admin_update_policies_have_using_and_with_check_guards():
    for policy in (
        "automated_savings_settings_update",
        "automated_savings_warehouses_update",
    ):
        body = MIGRATION.split(f"create policy {policy}", maxsplit=1)[1].split(
            ";", maxsplit=1
        )[0]
        assert "using (is_organization_admin(organization_id))" in body
        assert "with check (is_organization_admin(organization_id))" in body


def test_service_role_grants_keep_events_append_only():
    assert "revoke all on automated_savings_events from service_role;" in MIGRATION
    assert (
        "grant select, insert on automated_savings_events to service_role;" in MIGRATION
    )
    for role in ("public", "anon", "authenticated", "service_role"):
        assert (
            f"revoke all on sequence automated_savings_events_id_seq from {role};"
            in MIGRATION
        )
    assert (
        "grant usage, select on sequence automated_savings_events_id_seq "
        "to service_role;"
    ) in MIGRATION


def test_stale_enrollment_delete_matches_identity_and_version():
    body = _function_body("automated_savings_delete_stale_enrollment")
    assert "p_warehouse_created_on is not null" in body
    assert "w.warehouse_created_on = p_warehouse_created_on" in body
    assert "w.updated_at = p_enrollment_updated_at" in body


def test_disable_enrollment_updates_existing_exact_row_without_insert():
    body = _function_body("automated_savings_disable_enrollment")
    assert "returns boolean" in body
    assert "security invoker" in body
    assert "set search_path = ''" in body
    assert "update public.automated_savings_warehouses w" in body
    assert "w.organization_id = p_organization_id" in body
    assert "w.warehouse_name = p_warehouse_name" in body
    assert "set enabled = false" in body
    assert "return v_updated = 1" in body
    assert "insert into" not in body


def test_unreleased_sentinel_followups_are_folded_away():
    assert not (
        MIGRATIONS_DIR / "20260713223505_automated_savings_sentinel_confirmed.sql"
    ).exists()
    assert not (
        MIGRATIONS_DIR / "20260714002106_automated_savings_intent_safety.sql"
    ).exists()

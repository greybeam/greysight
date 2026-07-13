-- Deploy the atomic enroll/re-enroll RPC as its own migration.
--
-- The function body already lives in 202607120001_automated_savings.sql, but
-- that migration was applied to environments before the RPC was appended to it
-- — so `automated_savings_upsert_enrollment` is missing from those databases
-- and the enroll path (which POSTs to /rpc/automated_savings_upsert_enrollment)
-- fails with PostgREST PGRST202 ("Could not find the function ...") → 502.
--
-- `create or replace` is idempotent: safe whether the function is absent (fresh
-- deploy) or already present (a database that ran the original migration in
-- full). Kept byte-for-byte identical to the definition in the original
-- migration so the two never diverge.
create or replace function automated_savings_upsert_enrollment(
    p_organization_id uuid,
    p_warehouse_name text,
    p_enabled boolean,
    p_stored_default integer,
    p_managed_default integer,
    p_warehouse_created_on timestamptz
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into automated_savings_warehouses (
        organization_id, warehouse_name, enabled,
        stored_default_auto_suspend, managed_auto_suspend, warehouse_created_on
    )
    values (
        p_organization_id, p_warehouse_name, p_enabled,
        p_stored_default, p_managed_default, p_warehouse_created_on
    )
    on conflict (organization_id, warehouse_name) do update
    set enabled = excluded.enabled,
        warehouse_created_on = excluded.warehouse_created_on,
        stored_default_auto_suspend = case
            when automated_savings_warehouses.warehouse_created_on is not null
                 and automated_savings_warehouses.warehouse_created_on = excluded.warehouse_created_on
            then automated_savings_warehouses.stored_default_auto_suspend
            else excluded.stored_default_auto_suspend
        end,
        managed_auto_suspend = case
            when automated_savings_warehouses.warehouse_created_on is not null
                 and automated_savings_warehouses.warehouse_created_on = excluded.warehouse_created_on
            then automated_savings_warehouses.managed_auto_suspend
            else excluded.managed_auto_suspend
        end;
end;
$$;

revoke all on function automated_savings_upsert_enrollment(
    uuid, text, boolean, integer, integer, timestamptz
) from public;
grant execute on function automated_savings_upsert_enrollment(
    uuid, text, boolean, integer, integer, timestamptz
) to service_role;

-- Capture the live AUTO_SUSPEND value that authorized an intent. The worker
-- compares against it before retrying a sentinel or applying an admin reapply,
-- so a later customer edit is never overwritten.
alter table automated_savings_restore_intents
    add column if not exists expected_from integer;

-- Enforce the parent enrollment for all new intent writes. NOT VALID avoids
-- blocking deployment on any legacy orphan while still installing FK triggers
-- for concurrent writes immediately.
alter table automated_savings_restore_intents
    add constraint automated_savings_intents_enrollment_fkey
    foreign key (organization_id, warehouse_name)
    references automated_savings_warehouses (organization_id, warehouse_name)
    on delete cascade
    not valid;

create or replace function automated_savings_enqueue_reapply(
    p_organization_id uuid,
    p_warehouse_name text,
    p_restore_to integer,
    p_expected_from integer
)
returns boolean
language plpgsql
security invoker
set search_path = public
as $$
declare
    v_created boolean;
begin
    with eligible as materialized (
        select w.organization_id, w.warehouse_name
        from automated_savings_warehouses w
        where w.organization_id = p_organization_id
          and w.warehouse_name = p_warehouse_name
          and w.managed_auto_suspend is not distinct from p_restore_to
          and w.drift_state = 'drifted'
          and w.drifted_value is not distinct from p_expected_from
        for update
    ), inserted as (
        insert into automated_savings_restore_intents (
            organization_id, warehouse_name, restore_to, expected_from, kind
        )
        select e.organization_id, e.warehouse_name,
               p_restore_to, p_expected_from, 'reapply'
        from eligible e
        where not exists (
            select 1
            from automated_savings_restore_intents i
            where i.organization_id = e.organization_id
              and i.warehouse_name = e.warehouse_name
        )
        on conflict (organization_id, warehouse_name) do nothing
        returning 1
    )
    select exists(select 1 from inserted) into v_created;

    if v_created then
        update automated_savings_warehouses
        set drift_state = 'ok', drifted_value = null
        where organization_id = p_organization_id
          and warehouse_name = p_warehouse_name;
    end if;
    return v_created;
end;
$$;

revoke all on function automated_savings_enqueue_reapply(
    uuid, text, integer, integer
) from public;
grant execute on function automated_savings_enqueue_reapply(
    uuid, text, integer, integer
) to service_role;

create or replace function automated_savings_cleanup_intent(
    p_organization_id uuid,
    p_warehouse_name text,
    p_cycle_id uuid,
    p_kind text
)
returns boolean
language plpgsql
security invoker
set search_path = public
as $$
declare
    v_deleted integer;
begin
    perform 1
    from automated_savings_warehouses w
    where w.organization_id = p_organization_id
      and w.warehouse_name = p_warehouse_name
    for update;

    delete from automated_savings_restore_intents
    where organization_id = p_organization_id
      and warehouse_name = p_warehouse_name
      and cycle_id = p_cycle_id
      and kind = p_kind;
    get diagnostics v_deleted = row_count;

    if v_deleted <> 1 then
        return false;
    end if;

    delete from automated_savings_warehouses w
    where w.organization_id = p_organization_id
      and w.warehouse_name = p_warehouse_name
      and not exists (
          select 1
          from automated_savings_restore_intents i
          where i.organization_id = w.organization_id
            and i.warehouse_name = w.warehouse_name
      );
    return true;
end;
$$;

revoke all on function automated_savings_cleanup_intent(
    uuid, text, uuid, text
) from public;
grant execute on function automated_savings_cleanup_intent(
    uuid, text, uuid, text
) to service_role;

create or replace function automated_savings_cleanup_enrollment_if_no_intent(
    p_organization_id uuid,
    p_warehouse_name text
)
returns boolean
language plpgsql
security invoker
set search_path = public
as $$
declare
    v_deleted integer;
begin
    perform 1
    from automated_savings_warehouses w
    where w.organization_id = p_organization_id
      and w.warehouse_name = p_warehouse_name
    for update;

    delete from automated_savings_warehouses w
    where w.organization_id = p_organization_id
      and w.warehouse_name = p_warehouse_name
      and not exists (
          select 1
          from automated_savings_restore_intents i
          where i.organization_id = w.organization_id
            and i.warehouse_name = w.warehouse_name
      );
    get diagnostics v_deleted = row_count;
    return v_deleted = 1;
end;
$$;

revoke all on function automated_savings_cleanup_enrollment_if_no_intent(
    uuid, text
) from public;
grant execute on function automated_savings_cleanup_enrollment_if_no_intent(
    uuid, text
) to service_role;

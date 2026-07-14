-- Automated Savings: direct warehouse suspension settings, enrollment, and audit.

create table automated_savings_settings (
    organization_id uuid primary key references organizations(id) on delete cascade,
    agreed_at timestamptz,
    global_enabled boolean not null default false,
    grant_present boolean not null default false,
    grant_checked_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table automated_savings_warehouses (
    organization_id uuid not null references organizations(id) on delete cascade,
    warehouse_name text not null,
    enabled boolean not null default false,
    warehouse_created_on timestamptz not null,
    updated_at timestamptz not null default now(),
    primary key (organization_id, warehouse_name)
);

create table automated_savings_events (
    id bigint generated always as identity primary key,
    organization_id uuid not null references organizations(id) on delete cascade,
    warehouse_name text not null,
    action text not null check (action = 'suspend'),
    reason text not null check (reason = 'idle'),
    observed_state text not null,
    observed_running integer not null check (observed_running >= 0),
    observed_queued integer not null check (observed_queued >= 0),
    observed_quiescing integer not null check (observed_quiescing >= 0),
    observed_resumed_on timestamptz not null,
    observed_started_clusters integer,
    observed_min_cluster_count integer,
    observed_max_cluster_count integer,
    observed_at timestamptz not null,
    created_at timestamptz not null default now()
);

create trigger set_automated_savings_settings_updated_at
    before update on automated_savings_settings
    for each row execute function set_updated_at();

create trigger set_automated_savings_warehouses_updated_at
    before update on automated_savings_warehouses
    for each row execute function set_updated_at();

alter table automated_savings_settings enable row level security;
alter table automated_savings_warehouses enable row level security;
alter table automated_savings_events enable row level security;

create policy automated_savings_settings_read
    on automated_savings_settings for select to authenticated
    using (is_organization_member(organization_id));
create policy automated_savings_settings_insert
    on automated_savings_settings for insert to authenticated
    with check (is_organization_admin(organization_id));
create policy automated_savings_settings_update
    on automated_savings_settings for update to authenticated
    using (is_organization_admin(organization_id))
    with check (is_organization_admin(organization_id));

create policy automated_savings_warehouses_read
    on automated_savings_warehouses for select to authenticated
    using (is_organization_member(organization_id));
create policy automated_savings_warehouses_insert
    on automated_savings_warehouses for insert to authenticated
    with check (is_organization_admin(organization_id));
create policy automated_savings_warehouses_update
    on automated_savings_warehouses for update to authenticated
    using (is_organization_admin(organization_id))
    with check (is_organization_admin(organization_id));

create policy automated_savings_events_read
    on automated_savings_events for select to authenticated
    using (is_organization_member(organization_id));

-- Match table privileges to the RLS contract. Authenticated users receive only
-- policy-backed operations; the service role receives scoped settings/enrollment
-- access plus append-only event access; anonymous users receive none.
revoke all on automated_savings_settings from anon, authenticated;
revoke all on automated_savings_warehouses from anon, authenticated;
revoke all on automated_savings_events from anon, authenticated;
grant select, insert, update on automated_savings_settings to authenticated;
grant select, insert, update on automated_savings_warehouses to authenticated;
grant select on automated_savings_events to authenticated;
revoke all on automated_savings_settings from service_role;
revoke all on automated_savings_warehouses from service_role;
revoke all on automated_savings_events from service_role;
grant select, insert, update on automated_savings_settings to service_role;
grant select, insert, update, delete on automated_savings_warehouses to service_role;
grant select, insert on automated_savings_events to service_role;
revoke all on sequence automated_savings_events_id_seq from public;
revoke all on sequence automated_savings_events_id_seq from anon;
revoke all on sequence automated_savings_events_id_seq from authenticated;
revoke all on sequence automated_savings_events_id_seq from service_role;
grant usage, select on sequence automated_savings_events_id_seq to service_role;

create index automated_savings_events_org_created_idx
    on automated_savings_events (organization_id, created_at desc);
create index automated_savings_events_wh_created_idx
    on automated_savings_events (organization_id, warehouse_name, created_at desc);

create function automated_savings_upsert_enrollment(
    p_organization_id uuid,
    p_warehouse_name text,
    p_enabled boolean,
    p_warehouse_created_on timestamptz
)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
begin
    if p_warehouse_created_on is null then
        raise exception 'warehouse identity is required';
    end if;

    insert into public.automated_savings_warehouses (
        organization_id,
        warehouse_name,
        enabled,
        warehouse_created_on
    )
    values (
        p_organization_id,
        p_warehouse_name,
        p_enabled,
        p_warehouse_created_on
    )
    on conflict (organization_id, warehouse_name) do update
    set enabled = excluded.enabled,
        warehouse_created_on = excluded.warehouse_created_on;
end;
$$;

revoke execute on function public.automated_savings_upsert_enrollment(
    uuid, text, boolean, timestamptz
) from public;
revoke execute on function public.automated_savings_upsert_enrollment(
    uuid, text, boolean, timestamptz
) from anon;
revoke execute on function public.automated_savings_upsert_enrollment(
    uuid, text, boolean, timestamptz
) from authenticated;
grant execute on function public.automated_savings_upsert_enrollment(
    uuid, text, boolean, timestamptz
) to service_role;

create function automated_savings_disable_enrollment(
    p_organization_id uuid,
    p_warehouse_name text
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_updated integer;
begin
    update public.automated_savings_warehouses w
    set enabled = false
    where w.organization_id = p_organization_id
      and w.warehouse_name = p_warehouse_name;
    get diagnostics v_updated = row_count;
    return v_updated = 1;
end;
$$;

revoke execute on function public.automated_savings_disable_enrollment(
    uuid, text
) from public;
revoke execute on function public.automated_savings_disable_enrollment(
    uuid, text
) from anon;
revoke execute on function public.automated_savings_disable_enrollment(
    uuid, text
) from authenticated;
grant execute on function public.automated_savings_disable_enrollment(
    uuid, text
) to service_role;

create function automated_savings_authorize_suspend(
    p_organization_id uuid,
    p_warehouse_name text,
    p_warehouse_created_on timestamptz,
    p_enrollment_updated_at timestamptz
)
returns boolean
language sql
security invoker
set search_path = ''
as $$
    select exists (
        select 1
        from public.automated_savings_warehouses w
        join public.automated_savings_settings s
          on s.organization_id = w.organization_id
        where w.organization_id = p_organization_id
          and w.warehouse_name = p_warehouse_name
          and s.global_enabled
          and w.enabled
          and w.warehouse_created_on = p_warehouse_created_on
          and w.updated_at = p_enrollment_updated_at
    );
$$;

revoke execute on function public.automated_savings_authorize_suspend(
    uuid, text, timestamptz, timestamptz
) from public;
revoke execute on function public.automated_savings_authorize_suspend(
    uuid, text, timestamptz, timestamptz
) from anon;
revoke execute on function public.automated_savings_authorize_suspend(
    uuid, text, timestamptz, timestamptz
) from authenticated;
grant execute on function public.automated_savings_authorize_suspend(
    uuid, text, timestamptz, timestamptz
) to service_role;

create function automated_savings_delete_stale_enrollment(
    p_organization_id uuid,
    p_warehouse_name text,
    p_warehouse_created_on timestamptz,
    p_enrollment_updated_at timestamptz
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_deleted integer;
begin
    delete from public.automated_savings_warehouses w
    where p_warehouse_created_on is not null
      and p_enrollment_updated_at is not null
      and w.organization_id = p_organization_id
      and w.warehouse_name = p_warehouse_name
      and w.warehouse_created_on = p_warehouse_created_on
      and w.updated_at = p_enrollment_updated_at;
    get diagnostics v_deleted = row_count;
    return v_deleted = 1;
end;
$$;

revoke execute on function public.automated_savings_delete_stale_enrollment(
    uuid, text, timestamptz, timestamptz
) from public;
revoke execute on function public.automated_savings_delete_stale_enrollment(
    uuid, text, timestamptz, timestamptz
) from anon;
revoke execute on function public.automated_savings_delete_stale_enrollment(
    uuid, text, timestamptz, timestamptz
) from authenticated;
grant execute on function public.automated_savings_delete_stale_enrollment(
    uuid, text, timestamptz, timestamptz
) to service_role;

create function automated_savings_worker_tenants()
returns table (organization_id uuid)
language sql
security invoker
set search_path = ''
as $$
    select s.organization_id
    from public.automated_savings_settings s
    where s.global_enabled
      and exists (
          select 1
          from public.automated_savings_warehouses w
          where w.organization_id = s.organization_id
            and w.enabled
      );
$$;

revoke execute on function public.automated_savings_worker_tenants() from public;
revoke execute on function public.automated_savings_worker_tenants() from anon;
revoke execute on function public.automated_savings_worker_tenants() from authenticated;
grant execute on function public.automated_savings_worker_tenants() to service_role;

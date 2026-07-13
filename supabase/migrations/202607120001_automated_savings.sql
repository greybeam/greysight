-- Automated Savings: opt-in settings, per-warehouse enrollment, restore-intent sentinel.

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
    managed_auto_suspend integer check (managed_auto_suspend is null or managed_auto_suspend >= 60),
    stored_default_auto_suspend integer check (stored_default_auto_suspend is null or stored_default_auto_suspend not in (0, 1)),
    warehouse_created_on timestamptz,
    cooldown_ts timestamptz,
    drift_state text not null default 'ok' check (drift_state in ('ok','drifted','unsupported')),
    drifted_value integer,
    updated_at timestamptz not null default now(),
    primary key (organization_id, warehouse_name)
);
-- managed_auto_suspend: live restore target + drift baseline (editable via API, floor 60).
-- stored_default_auto_suspend: immutable opt-in capture, reference/audit only.

create table automated_savings_restore_intents (
    organization_id uuid not null references organizations(id) on delete cascade,
    warehouse_name text not null,
    restore_to integer not null,
    set_at timestamptz not null default now(),
    baseline_resumed_on timestamptz,
    primary key (organization_id, warehouse_name)
);
-- baseline_resumed_on: warehouse resumed_on captured at set-time. A later poll
-- observing an advanced resumed_on proves a suspend→resume cycle completed under
-- the sentinel, so reconcile restores early instead of holding for another cycle.

create trigger set_automated_savings_settings_updated_at
    before update on automated_savings_settings
    for each row execute function set_updated_at();

create trigger set_automated_savings_warehouses_updated_at
    before update on automated_savings_warehouses
    for each row execute function set_updated_at();

alter table automated_savings_settings enable row level security;
alter table automated_savings_warehouses enable row level security;
alter table automated_savings_restore_intents enable row level security;

-- Members read; only owners/admins mutate. Restore-intents are worker-only
-- (service role bypasses RLS); members may read them for status display.
create policy automated_savings_settings_read on automated_savings_settings
    for select to authenticated using (is_organization_member(organization_id));
create policy automated_savings_settings_write on automated_savings_settings
    for all to authenticated using (is_organization_admin(organization_id))
    with check (is_organization_admin(organization_id));

create policy automated_savings_warehouses_read on automated_savings_warehouses
    for select to authenticated using (is_organization_member(organization_id));
create policy automated_savings_warehouses_write on automated_savings_warehouses
    for all to authenticated using (is_organization_admin(organization_id))
    with check (is_organization_admin(organization_id));

create policy automated_savings_intents_read on automated_savings_restore_intents
    for select to authenticated using (is_organization_member(organization_id));
-- No authenticated write policy: intents are written only by the worker (service role).

-- Worker tenant enumeration: orgs with the global switch on and >=1 enrolled warehouse,
-- UNION orgs with any outstanding restore-intent (so a kill-switched org still drains).
create or replace function automated_savings_worker_tenants()
returns table (organization_id uuid)
language sql
security definer
set search_path = public
as $$
    select s.organization_id
    from automated_savings_settings s
    where s.global_enabled
      and exists (
          select 1 from automated_savings_warehouses w
          where w.organization_id = s.organization_id and w.enabled
      )
    union
    select i.organization_id
    from automated_savings_restore_intents i;
$$;

revoke all on function automated_savings_worker_tenants() from public;
grant execute on function automated_savings_worker_tenants() to service_role;

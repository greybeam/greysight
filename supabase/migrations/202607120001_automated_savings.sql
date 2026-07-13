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
    cycle_id uuid not null default gen_random_uuid(),
    kind text not null default 'sentinel' check (kind in ('sentinel', 'reapply')),
    primary key (organization_id, warehouse_name)
);
-- kind: 'sentinel' = worker's own AUTO_SUSPEND=1 suspend (live is 1/restore_to,
-- else drift). 'reapply' = admin asked (via API) to re-apply the managed default
-- over a drifted value — the worker overwrites the live value unconditionally and
-- never treats the mismatch as drift.
-- baseline_resumed_on: warehouse resumed_on captured at set-time. A later poll
-- observing an advanced resumed_on proves a suspend→resume cycle completed under
-- the sentinel, so reconcile restores early instead of holding for another cycle.
-- cycle_id: shared by a set_sentinel event and its matching restore event so the
-- audit log (automated_savings_events) can pair a suspend with its restore.

-- Append-only audit log of every AUTO_SUSPEND mutation the worker issues on a
-- customer warehouse. Never updated or deleted; feeds trust/debugging and the
-- future savings-analytics surface (pair a `set_sentinel` with its `restore` by
-- cycle_id to derive reclaimed idle seconds).
create table automated_savings_events (
    id bigint generated always as identity primary key,
    organization_id uuid not null references organizations(id) on delete cascade,
    warehouse_name text not null,
    cycle_id uuid,
    action text not null check (action in ('set_sentinel', 'restore')),
    reason text not null check (
        reason in ('decide', 'suspended', 'busy', 'resume_aware', 'aged_out', 'reconcile_reapply')
    ),
    from_value integer,
    to_value integer not null,
    observed_state text,
    observed_running integer,
    observed_queued integer,
    observed_resumed_on timestamptz,
    observed_at timestamptz not null,
    created_at timestamptz not null default now(),
    check (
        (action = 'set_sentinel' and reason = 'decide' and to_value = 1)
        or (action = 'restore'
            and reason in ('suspended', 'busy', 'resume_aware', 'aged_out', 'reconcile_reapply')
            and to_value >= 60)
    )
);

create trigger set_automated_savings_settings_updated_at
    before update on automated_savings_settings
    for each row execute function set_updated_at();

create trigger set_automated_savings_warehouses_updated_at
    before update on automated_savings_warehouses
    for each row execute function set_updated_at();

alter table automated_savings_settings enable row level security;
alter table automated_savings_warehouses enable row level security;
alter table automated_savings_restore_intents enable row level security;
alter table automated_savings_events enable row level security;

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

create policy automated_savings_events_read on automated_savings_events
    for select to authenticated using (is_organization_member(organization_id));
-- No authenticated write/update/delete policy: the audit log is append-only and
-- written only by the worker (service role bypasses RLS).

create index automated_savings_events_org_created_idx
    on automated_savings_events (organization_id, created_at desc);
create index automated_savings_events_wh_created_idx
    on automated_savings_events (organization_id, warehouse_name, created_at desc);

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

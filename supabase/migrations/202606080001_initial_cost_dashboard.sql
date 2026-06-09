create extension if not exists pgcrypto;

create table organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique,
  created_by_user_id uuid references auth.users(id) on delete set null default auth.uid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table organization_memberships (
  organization_id uuid not null references organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null check (role in ('owner', 'admin', 'member')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (organization_id, user_id)
);

create table snowflake_connections (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  display_name text not null,
  account_identifier text not null,
  username text not null,
  role_name text,
  warehouse_name text,
  database_name text not null default 'SNOWFLAKE',
  schema_name text not null default 'ACCOUNT_USAGE',
  credential_reference text not null,
  status text not null default 'pending' check (status in ('pending', 'valid', 'invalid')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table connection_validation_results (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  snowflake_connection_id uuid not null references snowflake_connections(id) on delete cascade,
  status text not null check (status in ('pending', 'succeeded', 'failed')),
  checks jsonb not null default '{}'::jsonb,
  error_code text,
  user_safe_message text,
  checked_at timestamptz not null default now()
);

create table analysis_runs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  snowflake_connection_id uuid references snowflake_connections(id) on delete set null,
  source text not null check (source in ('demo', 'snowflake')),
  status text not null check (status in ('queued', 'running', 'completed', 'failed', 'expired', 'deleted')),
  window_days integer not null check (window_days > 0 and window_days <= 365),
  aggregate_summary jsonb not null default '{}'::jsonb,
  error_code text,
  user_safe_message text,
  created_by_user_id uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz not null default now()
);

create table analysis_run_datasets (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null,
  organization_id uuid not null references organizations(id) on delete cascade,
  dataset_key text not null,
  aggregate_dataset jsonb not null,
  retention_expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (run_id, dataset_key),
  foreign key (run_id, organization_id)
    references analysis_runs(id, organization_id)
    on delete cascade
);

create table audit_events (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  actor_user_id uuid references auth.users(id) on delete set null,
  event_type text not null,
  event_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table dashboard_filter_preferences (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  preferences jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, user_id)
);

alter table analysis_runs
  add constraint analysis_runs_id_organization_id_unique unique (id, organization_id);

create index organization_memberships_user_id_idx
  on organization_memberships(user_id);
create index snowflake_connections_organization_id_idx
  on snowflake_connections(organization_id);
create index connection_validation_results_organization_id_idx
  on connection_validation_results(organization_id);
create index connection_validation_results_connection_checked_at_idx
  on connection_validation_results(snowflake_connection_id, checked_at desc);
create index analysis_runs_organization_created_at_idx
  on analysis_runs(organization_id, created_at desc);
create index analysis_runs_connection_created_at_idx
  on analysis_runs(snowflake_connection_id, created_at desc);
create index analysis_run_datasets_run_dataset_key_idx
  on analysis_run_datasets(run_id, dataset_key);
create index analysis_run_datasets_organization_retention_idx
  on analysis_run_datasets(organization_id, retention_expires_at);
create index audit_events_organization_created_at_idx
  on audit_events(organization_id, created_at desc);
create index dashboard_filter_preferences_user_idx
  on dashboard_filter_preferences(user_id);

create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger organizations_set_updated_at
  before update on organizations
  for each row execute function set_updated_at();
create trigger organization_memberships_set_updated_at
  before update on organization_memberships
  for each row execute function set_updated_at();
create trigger snowflake_connections_set_updated_at
  before update on snowflake_connections
  for each row execute function set_updated_at();
create trigger analysis_runs_set_updated_at
  before update on analysis_runs
  for each row execute function set_updated_at();
create trigger analysis_run_datasets_set_updated_at
  before update on analysis_run_datasets
  for each row execute function set_updated_at();
create trigger dashboard_filter_preferences_set_updated_at
  before update on dashboard_filter_preferences
  for each row execute function set_updated_at();

create or replace function create_organization_owner_membership()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.created_by_user_id is not null then
    insert into organization_memberships (organization_id, user_id, role)
    values (new.id, new.created_by_user_id, 'owner')
    on conflict (organization_id, user_id) do nothing;
  end if;

  return new;
end;
$$;

create trigger organizations_create_owner_membership
  after insert on organizations
  for each row execute function create_organization_owner_membership();

alter table organizations enable row level security;
alter table organization_memberships enable row level security;
alter table snowflake_connections enable row level security;
alter table connection_validation_results enable row level security;
alter table analysis_runs enable row level security;
alter table analysis_run_datasets enable row level security;
alter table audit_events enable row level security;
alter table dashboard_filter_preferences enable row level security;

create or replace function is_organization_member(target_organization_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select auth.uid() is not null
    and exists (
      select 1
      from organization_memberships
      where organization_memberships.organization_id = target_organization_id
        and organization_memberships.user_id = auth.uid()
    );
$$;

create or replace function is_organization_admin(target_organization_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select auth.uid() is not null
    and exists (
      select 1
      from organization_memberships
      where organization_memberships.organization_id = target_organization_id
        and organization_memberships.user_id = auth.uid()
        and organization_memberships.role in ('owner', 'admin')
    );
$$;

revoke all on function is_organization_member(uuid) from public;
revoke all on function is_organization_admin(uuid) from public;
grant execute on function is_organization_member(uuid) to authenticated;
grant execute on function is_organization_admin(uuid) to authenticated;

create policy organizations_select_for_members
  on organizations for select
  to authenticated
  using (is_organization_member(id));

create policy organizations_insert_for_authenticated
  on organizations for insert
  to authenticated
  with check (
    auth.uid() is not null
    and created_by_user_id = auth.uid()
  );

create policy organizations_update_for_admins
  on organizations for update
  to authenticated
  using (is_organization_admin(id))
  with check (is_organization_admin(id));

create policy organization_memberships_select_for_members
  on organization_memberships for select
  to authenticated
  using (is_organization_member(organization_id));

create policy organization_memberships_insert_for_admins
  on organization_memberships for insert
  to authenticated
  with check (is_organization_admin(organization_id));

create policy organization_memberships_update_for_admins
  on organization_memberships for update
  to authenticated
  using (is_organization_admin(organization_id))
  with check (is_organization_admin(organization_id));

create policy organization_memberships_delete_for_admins
  on organization_memberships for delete
  to authenticated
  using (is_organization_admin(organization_id));

create policy snowflake_connections_select_for_members
  on snowflake_connections for select
  to authenticated
  using (is_organization_member(organization_id));

create policy snowflake_connections_insert_for_admins
  on snowflake_connections for insert
  to authenticated
  with check (is_organization_admin(organization_id));

create policy snowflake_connections_update_for_admins
  on snowflake_connections for update
  to authenticated
  using (is_organization_admin(organization_id))
  with check (is_organization_admin(organization_id));

create policy snowflake_connections_delete_for_admins
  on snowflake_connections for delete
  to authenticated
  using (is_organization_admin(organization_id));

create policy connection_validation_results_select_for_members
  on connection_validation_results for select
  to authenticated
  using (is_organization_member(organization_id));

create policy connection_validation_results_insert_for_admins
  on connection_validation_results for insert
  to authenticated
  with check (is_organization_admin(organization_id));

create policy connection_validation_results_update_for_admins
  on connection_validation_results for update
  to authenticated
  using (is_organization_admin(organization_id))
  with check (is_organization_admin(organization_id));

create policy connection_validation_results_delete_for_admins
  on connection_validation_results for delete
  to authenticated
  using (is_organization_admin(organization_id));

create policy analysis_runs_select_for_members
  on analysis_runs for select
  to authenticated
  using (is_organization_member(organization_id));

create policy analysis_runs_insert_for_admins
  on analysis_runs for insert
  to authenticated
  with check (is_organization_admin(organization_id));

create policy analysis_runs_update_for_admins
  on analysis_runs for update
  to authenticated
  using (is_organization_admin(organization_id))
  with check (is_organization_admin(organization_id));

create policy analysis_runs_delete_for_admins
  on analysis_runs for delete
  to authenticated
  using (is_organization_admin(organization_id));

create policy analysis_run_datasets_select_for_members
  on analysis_run_datasets for select
  to authenticated
  using (is_organization_member(organization_id));

create policy analysis_run_datasets_insert_for_admins
  on analysis_run_datasets for insert
  to authenticated
  with check (is_organization_admin(organization_id));

create policy analysis_run_datasets_update_for_admins
  on analysis_run_datasets for update
  to authenticated
  using (is_organization_admin(organization_id))
  with check (is_organization_admin(organization_id));

create policy analysis_run_datasets_delete_for_admins
  on analysis_run_datasets for delete
  to authenticated
  using (is_organization_admin(organization_id));

create policy audit_events_select_for_members
  on audit_events for select
  to authenticated
  using (is_organization_member(organization_id));

create policy audit_events_insert_for_admins
  on audit_events for insert
  to authenticated
  with check (is_organization_admin(organization_id));

create policy dashboard_filter_preferences_select_for_owner
  on dashboard_filter_preferences for select
  to authenticated
  using (
    is_organization_member(organization_id)
    and user_id = auth.uid()
  );

create policy dashboard_filter_preferences_insert_for_owner
  on dashboard_filter_preferences for insert
  to authenticated
  with check (
    is_organization_member(organization_id)
    and user_id = auth.uid()
  );

create policy dashboard_filter_preferences_update_for_owner
  on dashboard_filter_preferences for update
  to authenticated
  using (
    is_organization_member(organization_id)
    and user_id = auth.uid()
  )
  with check (
    is_organization_member(organization_id)
    and user_id = auth.uid()
  );

create policy dashboard_filter_preferences_delete_for_owner
  on dashboard_filter_preferences for delete
  to authenticated
  using (
    is_organization_member(organization_id)
    and user_id = auth.uid()
  );

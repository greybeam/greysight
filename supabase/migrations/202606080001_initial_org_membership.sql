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

create index organization_memberships_user_id_idx
  on organization_memberships(user_id);

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

-- Org creation is service-role/RPC-only (no authenticated INSERT policy). Self-
-- service org creation goes through a validated service-role path and operator
-- seeding uses service-role inserts; both bypass RLS, so the owner-membership
-- trigger still fires. See docs/auth-and-deployment.md, "First-user bootstrap".

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

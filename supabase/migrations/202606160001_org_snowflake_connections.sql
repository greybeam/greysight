-- Per-org Snowflake connection metadata. The RSA private key + passphrase are
-- NOT stored here; they live in Supabase Vault and are referenced by secret_id.
-- See docs/superpowers/specs/2026-06-16-per-org-snowflake-onboarding-design.md.

-- Vault is required for the secret RPCs below; fail the migration loudly here
-- rather than at first secret write if the target project hasn't enabled it.
create extension if not exists supabase_vault with schema vault;

create table organization_snowflake_connections (
  organization_id uuid primary key references organizations(id) on delete cascade,
  account text not null,
  snowflake_user text not null,
  role text not null,
  warehouse text not null,
  database text,
  schema text,
  secret_id uuid,
  has_passphrase boolean not null default false,
  status text not null default 'invalid' check (status in ('active', 'invalid')),
  last_validated_at timestamptz,
  created_by_user_id uuid references auth.users(id) on delete set null default auth.uid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger organization_snowflake_connections_set_updated_at
  before update on organization_snowflake_connections
  for each row execute function set_updated_at();

alter table organization_snowflake_connections enable row level security;
-- No authenticated INSERT/UPDATE/DELETE policy: all writes are service-role-side
-- via the RPCs below, mirroring the organizations INSERT lockdown.

-- Member-facing read path: non-sensitive fields only (no secret_id).
create or replace function get_org_connection_summary(target_organization_id uuid)
returns table (
  organization_id uuid,
  account text,
  status text,
  last_validated_at timestamptz,
  has_passphrase boolean
)
language sql
stable
security definer
set search_path = public
as $$
  select c.organization_id, c.account, c.status, c.last_validated_at, c.has_passphrase
  from organization_snowflake_connections c
  where c.organization_id = target_organization_id
    and is_organization_member(target_organization_id);
$$;

revoke all on function get_org_connection_summary(uuid) from public;
grant execute on function get_org_connection_summary(uuid) to authenticated;

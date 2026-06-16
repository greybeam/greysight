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

-- Vault secret helpers. service_role only; these read/write the vault schema,
-- which is not exposed via PostgREST directly.

create or replace function set_organization_snowflake_secret(
  target_organization_id uuid,
  private_key_pem text,
  passphrase text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  existing_secret_id uuid;
  secret_payload text := json_build_object('pem', private_key_pem, 'passphrase', passphrase)::text;
  secret_name text := 'snowflake_pk_' || target_organization_id::text;
begin
  select secret_id into existing_secret_id
  from organization_snowflake_connections
  where organization_id = target_organization_id;

  if existing_secret_id is null then
    return vault.create_secret(secret_payload, secret_name, 'Greysight Snowflake key');
  else
    perform vault.update_secret(existing_secret_id, secret_payload, secret_name, 'Greysight Snowflake key');
    return existing_secret_id;
  end if;
end;
$$;

create or replace function get_organization_snowflake_secret(target_organization_id uuid)
returns table (private_key_pem text, passphrase text)
language plpgsql
security definer
set search_path = public, vault
as $$
declare
  target_secret_id uuid;
  decrypted text;
begin
  select secret_id into target_secret_id
  from organization_snowflake_connections
  where organization_id = target_organization_id;

  if target_secret_id is null then
    return;  -- no rows; caller treats as "no secret"
  end if;

  select decrypted_secret into decrypted
  from vault.decrypted_secrets
  where id = target_secret_id;

  if decrypted is null then
    return;
  end if;

  return query
    select decrypted::json ->> 'pem', decrypted::json ->> 'passphrase';
end;
$$;

create or replace function delete_organization_snowflake_secret(target_organization_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  target_secret_id uuid;
begin
  select secret_id into target_secret_id
  from organization_snowflake_connections
  where organization_id = target_organization_id;

  if target_secret_id is not null then
    delete from vault.secrets where id = target_secret_id;
  end if;
end;
$$;

revoke all on function set_organization_snowflake_secret(uuid, text, text) from public;
revoke all on function get_organization_snowflake_secret(uuid) from public;
revoke all on function delete_organization_snowflake_secret(uuid) from public;
grant execute on function set_organization_snowflake_secret(uuid, text, text) to service_role;
grant execute on function get_organization_snowflake_secret(uuid) to service_role;
grant execute on function delete_organization_snowflake_secret(uuid) to service_role;

-- Teardown safety net (Codex CRITICAL): deleting an org cascades the connection
-- row, and a future explicit disconnect deletes it too. In BOTH cases the Vault
-- secret must die with the row, or it is orphaned with no surviving reference.
-- A BEFORE DELETE trigger guarantees this regardless of how the row is removed.
create or replace function delete_org_snowflake_secret_on_row_delete()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if old.secret_id is not null then
    delete from vault.secrets where id = old.secret_id;
  end if;
  return old;
end;
$$;

create trigger organization_snowflake_connections_delete_secret
  before delete on organization_snowflake_connections
  for each row execute function delete_org_snowflake_secret_on_row_delete();

-- Atomic disconnect (Codex CRITICAL): a disconnect must leave the row TRUTHFUL.
-- Deleting only the Vault secret (the old plan) left status='active' with a dead
-- secret_id, so summaries reported "connected" while the resolver failed closed.
-- Here we delete the secret, clear secret_id, and flip status to 'invalid' in one
-- transaction. (Row deletion / org delete is handled by the trigger above.)
-- Intentionally IDEMPOTENT: if the org has no connection row (or already
-- disconnected), secret_id is null, the delete is skipped, and the update touches
-- zero rows — the function is a no-op and returns normally (no error). Callers
-- can safely disconnect twice; the route returns 204 either way.
create or replace function disconnect_organization_snowflake(target_organization_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  target_secret_id uuid;
begin
  select secret_id into target_secret_id
  from organization_snowflake_connections
  where organization_id = target_organization_id;

  if target_secret_id is not null then
    delete from vault.secrets where id = target_secret_id;
  end if;

  update organization_snowflake_connections
    set secret_id = null, status = 'invalid'
    where organization_id = target_organization_id;
end;
$$;

revoke all on function disconnect_organization_snowflake(uuid) from public;
grant execute on function disconnect_organization_snowflake(uuid) to service_role;

-- v1 ownership guard: a user may OWN at most one org. Multi-org *membership*
-- (being an admin/member of other orgs) is intentionally still allowed — this
-- index only caps ownership, which is what onboarding grants. Routing picks the
-- first membership today (org switcher deferred); see TODO in Notes.
-- Removing this index (plus the API guard) is all that's needed to let a user
-- own multiple orgs later.
--
-- Preflight (Codex MEDIUM): a live project with two pre-existing owner rows for
-- one user would fail the unique-index build with an opaque error. Fail loudly
-- with an actionable message instead.
do $$
begin
  if exists (
    select 1 from organization_memberships
    where role = 'owner'
    group by user_id
    having count(*) > 1
  ) then
    raise exception 'Cannot create one_owner_membership_per_user: a user already owns >1 org. Resolve duplicate owner memberships before applying this migration.';
  end if;
end $$;

create unique index one_owner_membership_per_user
  on organization_memberships(user_id)
  where role = 'owner';

create or replace function create_org_with_snowflake_connection(
  p_user_id uuid,
  p_org_name text,
  p_account text,
  p_user text,
  p_role text,
  p_warehouse text,
  p_database text,
  p_schema text,
  p_private_key_pem text,
  p_passphrase text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  new_org_id uuid;
  new_secret_id uuid;
begin
  -- Serialize concurrent onboarding for the same user so the one-org guard
  -- cannot be raced. Lock is released at transaction end.
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));

  if exists (
    select 1 from organization_memberships
    where user_id = p_user_id and role = 'owner'
  ) then
    raise exception 'user already owns an organization'
      using errcode = 'unique_violation';
  end if;

  insert into organizations (name, created_by_user_id)
  values (p_org_name, p_user_id)
  returning id into new_org_id;
  -- organizations_create_owner_membership trigger inserts the owner membership.

  -- Insert the connection row first (without secret), then attach the secret so
  -- set_organization_snowflake_secret can find the row to update.
  insert into organization_snowflake_connections (
    organization_id, account, snowflake_user, role, warehouse,
    database, schema, has_passphrase, status, last_validated_at, created_by_user_id
  )
  values (
    new_org_id, p_account, p_user, p_role, p_warehouse,
    nullif(p_database, ''), nullif(p_schema, ''),
    p_passphrase is not null and p_passphrase <> '',
    'active', now(), p_user_id
  );

  new_secret_id := set_organization_snowflake_secret(new_org_id, p_private_key_pem, p_passphrase);
  update organization_snowflake_connections
    set secret_id = new_secret_id
    where organization_id = new_org_id;

  return new_org_id;
end;
$$;

revoke all on function create_org_with_snowflake_connection(uuid, text, text, text, text, text, text, text, text, text) from public;
grant execute on function create_org_with_snowflake_connection(uuid, text, text, text, text, text, text, text, text, text) to service_role;

-- Multi-org account switcher (#16).
--
-- 1. Lift the v1 one-owner cap so a user can own multiple orgs (each "Add
--    Account" creates a new owned org).
-- 2. Block two orgs from holding the SAME Snowflake account *concurrently* via a
--    partial unique index on upper(account), scoped to active connections.
--    Disconnect sets status='invalid' and keeps the row, so scoping to 'active'
--    lets a disconnected account be re-onboarded elsewhere.

drop index if exists one_owner_membership_per_user;

-- Preflight: a live project with two orgs already sharing an active account
-- would fail the unique-index build with an opaque error. Fail loudly instead.
do $$
begin
  if exists (
    select 1 from organization_snowflake_connections
    where status = 'active'
    group by upper(account)
    having count(*) > 1
  ) then
    raise exception 'Cannot create org_active_account_unique: an account is already active on >1 org. Resolve duplicates before applying this migration.';
  end if;
end $$;

create unique index org_active_account_unique
  on organization_snowflake_connections (upper(account))
  where status = 'active';

-- Redefine the create RPC: no owner guard, no per-user advisory lock (the unique
-- index now enforces account dedup atomically). Body otherwise unchanged from
-- 202606160001.
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
  insert into organizations (name, created_by_user_id)
  values (p_org_name, p_user_id)
  returning id into new_org_id;
  -- organizations_create_owner_membership trigger inserts the owner membership.

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

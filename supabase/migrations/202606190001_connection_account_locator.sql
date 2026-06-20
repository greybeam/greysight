-- 202606190001_connection_account_locator.sql
-- Persist the Snowflake account locator (current_account()) captured at
-- connection validation, so dashboard org-usage queries bind it directly
-- instead of running a serial current_account() pre-query. Nullable: legacy
-- rows use a one-time run-only runtime fallback until re-validated.

alter table organization_snowflake_connections
  add column if not exists account_locator text;

-- Drop the original 10-arg overload so create-or-replace below leaves exactly
-- one function. PostgreSQL keys functions by argument signature, so adding a
-- parameter would otherwise create a second overload (PostgREST PGRST203).
drop function if exists create_org_with_snowflake_connection(
  uuid, text, text, text, text, text, text, text, text, text
);

-- Recreate the create RPC with a trailing account_locator param (default null
-- keeps any existing callers working). Body mirrors the original plus the new
-- column in the connection insert.
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
  p_passphrase text,
  p_account_locator text default null
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
    database, schema, has_passphrase, status, last_validated_at,
    created_by_user_id, account_locator
  )
  values (
    new_org_id, p_account, p_user, p_role, p_warehouse,
    nullif(p_database, ''), nullif(p_schema, ''),
    p_passphrase is not null and p_passphrase <> '',
    'active', now(), p_user_id, nullif(p_account_locator, '')
  );

  new_secret_id := set_organization_snowflake_secret(new_org_id, p_private_key_pem, p_passphrase);
  update organization_snowflake_connections
    set secret_id = new_secret_id
    where organization_id = new_org_id;

  return new_org_id;
end;
$$;

revoke all on function create_org_with_snowflake_connection(uuid, text, text, text, text, text, text, text, text, text, text) from public;
grant execute on function create_org_with_snowflake_connection(uuid, text, text, text, text, text, text, text, text, text, text) to service_role;

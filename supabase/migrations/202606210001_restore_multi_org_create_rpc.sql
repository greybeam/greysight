-- 202606210001_restore_multi_org_create_rpc.sql
-- Fix a regression: 202606190001_connection_account_locator recreated
-- create_org_with_snowflake_connection from the ORIGINAL 202606160001 body,
-- which re-introduced the v1 one-owner cap (the per-user advisory lock and the
-- "user already owns an organization" guard). That cap was deliberately lifted
-- by 202606180001_multi_org_account_switcher so each "Add Account" creates a new
-- owned org. The guard raised 23505, surfaced to the API as a non-dedup 409, and
-- was mislabeled as a 502 ("Something went wrong") in onboarding.
--
-- Recreate the function with the account_locator column/param (from 19) but with
-- NO owner guard and NO advisory lock (account dedup is enforced atomically by
-- the org_active_account_unique partial unique index from 18). Body otherwise
-- mirrors 202606190001.

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

-- Invite/attach a user to an org by email. Returns a status the API maps to an
-- action (send invite, resend link, attach silently) and HTTP code. SECURITY
-- DEFINER so it can read auth.users; search_path='' + fully-qualified names +
-- service_role-only execute keep the privilege surface minimal.
create or replace function add_org_member_by_email(
  p_actor_user_id uuid,
  p_org_id uuid,
  p_email text
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_user_id uuid;
  v_confirmed timestamptz;
  v_is_admin boolean;
begin
  -- Defense in depth behind the API's require_org_admin: the actor must be an
  -- owner/admin of the target org.
  select exists (
    select 1
    from public.organization_memberships m
    where m.organization_id = p_org_id
      and m.user_id = p_actor_user_id
      and m.role in ('owner', 'admin')
  ) into v_is_admin;

  if not v_is_admin then
    return 'unauthorized';
  end if;

  select u.id, u.email_confirmed_at
    into v_user_id, v_confirmed
  from auth.users u
  where lower(u.email) = lower(p_email)
  limit 1;

  if v_user_id is null then
    return 'invite_needed';
  end if;

  if exists (
    select 1
    from public.organization_memberships m
    where m.organization_id = p_org_id
      and m.user_id = v_user_id
  ) then
    if v_confirmed is null then
      return 'pending_resend';
    end if;
    return 'already_member';
  end if;

  insert into public.organization_memberships (organization_id, user_id, role)
  values (p_org_id, v_user_id, 'member')
  on conflict (organization_id, user_id) do nothing;

  return 'added';
end;
$$;

revoke all on function add_org_member_by_email(uuid, uuid, text) from public;
revoke all on function add_org_member_by_email(uuid, uuid, text) from anon;
revoke all on function add_org_member_by_email(uuid, uuid, text) from authenticated;
grant execute on function add_org_member_by_email(uuid, uuid, text) to service_role;

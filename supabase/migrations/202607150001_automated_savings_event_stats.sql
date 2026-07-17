-- Auto Savings event stats: keyset-pagination index and service-role read RPCs.
-- Tenancy for these reads is enforced by the API (require_org_membership); the
-- RPCs are therefore executable by service_role only.

create index automated_savings_events_org_created_id_idx
    on public.automated_savings_events (organization_id, created_at desc, id desc);

drop index public.automated_savings_events_org_created_idx;

create function automated_savings_daily_suspensions(
    p_organization_id uuid,
    p_day_count int,
    p_end_day date
)
returns table (day date, warehouse_name text, suspension_count bigint)
language sql
security invoker
set search_path = ''
as $$
    select
        (e.created_at at time zone 'utc')::date as day,
        e.warehouse_name,
        count(*) as suspension_count
    from public.automated_savings_events e
    where e.organization_id = p_organization_id
      and e.action = 'suspend'
      and e.created_at >= (
          (p_end_day - (p_day_count - 1))::timestamp at time zone 'utc'
      )
      and e.created_at < ((p_end_day + 1)::timestamp at time zone 'utc')
    group by 1, 2
    order by 1, 2;
$$;

revoke execute on function public.automated_savings_daily_suspensions(
    uuid, int, date
) from public;
revoke execute on function public.automated_savings_daily_suspensions(
    uuid, int, date
) from anon;
revoke execute on function public.automated_savings_daily_suspensions(
    uuid, int, date
) from authenticated;
grant execute on function public.automated_savings_daily_suspensions(
    uuid, int, date
) to service_role;

create function automated_savings_events_page(
    p_organization_id uuid,
    p_page_limit int,
    p_cursor_created_at timestamptz default null,
    p_cursor_id bigint default null
)
returns table (
    id bigint,
    created_at timestamptz,
    warehouse_name text,
    action text,
    reason text,
    observed_started_clusters int,
    observed_resumed_on timestamptz,
    observed_at timestamptz
)
language sql
security invoker
set search_path = ''
as $$
    select
        e.id,
        e.created_at,
        e.warehouse_name,
        e.action,
        e.reason,
        e.observed_started_clusters,
        e.observed_resumed_on,
        e.observed_at
    from public.automated_savings_events e
    where e.organization_id = p_organization_id
      and (
          p_cursor_created_at is null
          or (e.created_at, e.id) < (p_cursor_created_at, p_cursor_id)
      )
    order by e.created_at desc, e.id desc
    limit p_page_limit;
$$;

revoke execute on function public.automated_savings_events_page(
    uuid, int, timestamptz, bigint
) from public;
revoke execute on function public.automated_savings_events_page(
    uuid, int, timestamptz, bigint
) from anon;
revoke execute on function public.automated_savings_events_page(
    uuid, int, timestamptz, bigint
) from authenticated;
grant execute on function public.automated_savings_events_page(
    uuid, int, timestamptz, bigint
) to service_role;

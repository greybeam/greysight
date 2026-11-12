-- Auto Savings: estimated savings.
--
-- 1. Persist the warehouse settings the counterfactual needs at suspend time
--    (size, AUTO_SUSPEND, scaling policy). All nullable: events written before
--    this migration fall back to assumed defaults.
-- 2. A service-role read RPC that estimates, per UTC day and warehouse, how
--    much compute each suspension avoided.
--
-- Counterfactual model, per suspension event. Without Greysight, an idle
-- warehouse running N clusters (min cluster count M) would have:
--   a. scaled in one cluster every `step` seconds until it reached M clusters.
--      AUTO_SUSPEND plays no part while more than M clusters are running; the
--      scaling policy governs scale-in. Snowflake documents STANDARD as ~2-3
--      consecutive 1-minute checks per cluster and ECONOMY as ~5-6; observed
--      behaviour is slower, so defaults are 200s (≈30 min from 10 → 1) and
--      360s. Both are empirical knobs to calibrate later.
--   b. then, at M clusters, run another AUTO_SUSPEND seconds before suspending.
--   Total counterfactual idle tail T = (N - M) * step + AUTO_SUSPEND, with a
--   cluster count that steps down N, N-1, ..., M over time.
--
-- Our suspend lands `idle_already` seconds into that tail. It is ~0 for a
-- warehouse suspended well past the uptime floor (the worker polls every few
-- seconds); one suspended at the floor may have been idle all session, so we
-- conservatively assume it was. If the warehouse was next resumed before the
-- tail ran out, it would have stayed up to serve that work, so the tail is cut
-- at the next resume.
--
--   avoided cluster-seconds = ∫ clusters(t) dt over
--                             [idle_already, min(T, idle_already + gap)]
--   credits = avoided cluster-seconds / 3600 * credits_per_hour(size)
--
-- Single-cluster warehouses (N = M = 1) reduce to
-- min(AUTO_SUSPEND - idle_already, gap).
--
-- 60-second minimum: Snowflake bills every cluster start for at least 60s.
-- The M minimum clusters start with the warehouse and have already run >= 62s
-- when we suspend (the uptime floor), so they never hit it. The N - M clusters
-- added during the spike may have started moments before our suspend and be
-- billed for unused minimum time that the counterfactual (where they keep
-- running) would not have wasted. Rough estimate: deduct
-- p_cluster_min_billing_penalty_seconds (default 30s, the midpoint of 0-60s)
-- cluster-seconds per added cluster, per suspension. Savings per suspension
-- are floored at 0.

alter table public.automated_savings_events
    add column observed_size text,
    add column observed_auto_suspend integer
        check (observed_auto_suspend is null or observed_auto_suspend >= 0),
    add column observed_scaling_policy text;

-- Cumulative cluster-seconds from the start of the idle tail to time t, for a
-- warehouse stepping down from n to m clusters every `step` seconds and then
-- holding m clusters for `hold` seconds.
create function automated_savings_tail_cluster_seconds(
    p_t double precision,
    p_n int,
    p_m int,
    p_step double precision,
    p_hold double precision
)
returns double precision
language plpgsql
immutable
security invoker
set search_path = ''
as $$
declare
    v_steps int := greatest(p_n - p_m, 0);
    v_step_end double precision := v_steps * p_step;
    v_t double precision := least(greatest(p_t, 0), v_step_end + greatest(p_hold, 0));
    v_k int;
begin
    if v_t < v_step_end then
        v_k := floor(v_t / p_step)::int;
        return p_step * (v_k * p_n - v_k * (v_k - 1) / 2.0)
            + (v_t - v_k * p_step) * (p_n - v_k);
    end if;
    return p_step * (v_steps * p_n - v_steps * (v_steps - 1) / 2.0)
        + (v_t - v_step_end) * p_m;
end;
$$;

revoke execute on function public.automated_savings_tail_cluster_seconds(
    double precision, int, int, double precision, double precision
) from public;
revoke execute on function public.automated_savings_tail_cluster_seconds(
    double precision, int, int, double precision, double precision
) from anon;
revoke execute on function public.automated_savings_tail_cluster_seconds(
    double precision, int, int, double precision, double precision
) from authenticated;
grant execute on function public.automated_savings_tail_cluster_seconds(
    double precision, int, int, double precision, double precision
) to service_role;

create function automated_savings_daily_savings(
    p_organization_id uuid,
    p_day_count int,
    p_end_day date,
    p_now timestamptz,
    p_uptime_floor_seconds int default 62,
    p_floor_slack_seconds int default 10,
    p_default_auto_suspend_seconds int default 600,
    p_never_suspend_cap_seconds int default 3600,
    p_standard_step_seconds int default 200,
    p_economy_step_seconds int default 360,
    p_cluster_min_billing_penalty_seconds int default 30
)
returns table (
    day date,
    warehouse_name text,
    suspension_count bigint,
    avoided_seconds double precision,
    avoided_credits double precision,
    assumed_settings_count bigint
)
language sql
security invoker
set search_path = ''
as $$
    with bounds as (
        select
            (p_end_day - (p_day_count - 1))::timestamp at time zone 'utc'
                as start_ts,
            (p_end_day + 1)::timestamp at time zone 'utc' as end_ts
    ),
    -- One row per warehouse session: a retried/duplicate suspend of the same
    -- session (same resumed_on) must not be counted twice.
    sessions as (
        select distinct on (e.warehouse_name, e.observed_resumed_on)
            e.id,
            e.created_at,
            e.warehouse_name,
            e.observed_resumed_on,
            e.observed_at,
            e.observed_started_clusters,
            e.observed_min_cluster_count,
            e.observed_size,
            e.observed_auto_suspend,
            e.observed_scaling_policy
        from public.automated_savings_events e, bounds b
        where e.organization_id = p_organization_id
          and e.action = 'suspend'
          and e.created_at >= b.start_ts
        order by e.warehouse_name, e.observed_resumed_on, e.observed_at, e.id
    ),
    sequenced as (
        select
            s.*,
            lead(s.observed_resumed_on) over (
                partition by s.warehouse_name
                order by s.observed_resumed_on, s.observed_at, s.id
            ) as next_resumed_on,
            extract(epoch from (s.observed_at - s.observed_resumed_on))
                as uptime_seconds,
            case lower(regexp_replace(coalesce(s.observed_size, ''), '[^a-zA-Z0-9]', '', 'g'))
                when 'xsmall' then 1
                when 'small' then 2
                when 'medium' then 4
                when 'large' then 8
                when 'xlarge' then 16
                when '2xlarge' then 32
                when 'xxlarge' then 32
                when '3xlarge' then 64
                when 'xxxlarge' then 64
                when '4xlarge' then 128
                when '5xlarge' then 256
                when '6xlarge' then 512
            end as size_credits_per_hour,
            greatest(coalesce(s.observed_min_cluster_count, 1), 1) as min_clusters
        from sessions s
    ),
    shaped as (
        select
            q.*,
            greatest(coalesce(q.observed_started_clusters, 1), q.min_clusters)
                as start_clusters,
            case upper(coalesce(q.observed_scaling_policy, ''))
                when 'ECONOMY' then p_economy_step_seconds
                else p_standard_step_seconds
            end::double precision as step_seconds,
            case
                when q.observed_auto_suspend is null
                    then p_default_auto_suspend_seconds
                when q.observed_auto_suspend = 0
                    then p_never_suspend_cap_seconds
                else q.observed_auto_suspend
            end::double precision as hold_seconds,
            case
                when q.uptime_seconds
                    <= p_uptime_floor_seconds + p_floor_slack_seconds
                    then q.uptime_seconds
                else 0
            end::double precision as idle_already_seconds,
            extract(epoch from (
                case
                    when q.next_resumed_on > q.observed_at then q.next_resumed_on
                    else greatest(p_now, q.observed_at)
                end - q.observed_at
            ))::double precision as gap_seconds
        from sequenced q
    ),
    windowed as (
        select
            x.*,
            (x.start_clusters - x.min_clusters) * x.step_seconds + x.hold_seconds
                as tail_seconds
        from shaped x
    ),
    avoided as (
        select
            w.created_at,
            w.warehouse_name,
            greatest(
                least(w.tail_seconds, w.idle_already_seconds + w.gap_seconds)
                    - least(w.idle_already_seconds, w.tail_seconds),
                0
            ) as avoided_seconds,
            greatest(
                public.automated_savings_tail_cluster_seconds(
                    least(w.tail_seconds, w.idle_already_seconds + w.gap_seconds),
                    w.start_clusters, w.min_clusters, w.step_seconds, w.hold_seconds
                )
                - public.automated_savings_tail_cluster_seconds(
                    w.idle_already_seconds,
                    w.start_clusters, w.min_clusters, w.step_seconds, w.hold_seconds
                )
                - (w.start_clusters - w.min_clusters)
                    * p_cluster_min_billing_penalty_seconds,
                0
            ) as avoided_cluster_seconds,
            coalesce(w.size_credits_per_hour, 1) as credits_per_cluster_hour,
            (w.size_credits_per_hour is null
                or w.observed_auto_suspend is null) as assumed
        from windowed w
    )
    select
        (a.created_at at time zone 'utc')::date as day,
        a.warehouse_name,
        count(*) as suspension_count,
        sum(a.avoided_seconds) as avoided_seconds,
        sum(a.avoided_cluster_seconds / 3600.0 * a.credits_per_cluster_hour)
            as avoided_credits,
        count(*) filter (where a.assumed) as assumed_settings_count
    from avoided a, bounds b
    where a.created_at < b.end_ts
    group by 1, 2
    order by 1, 2;
$$;

revoke execute on function public.automated_savings_daily_savings(
    uuid, int, date, timestamptz, int, int, int, int, int, int, int
) from public;
revoke execute on function public.automated_savings_daily_savings(
    uuid, int, date, timestamptz, int, int, int, int, int, int, int
) from anon;
revoke execute on function public.automated_savings_daily_savings(
    uuid, int, date, timestamptz, int, int, int, int, int, int, int
) from authenticated;
grant execute on function public.automated_savings_daily_savings(
    uuid, int, date, timestamptz, int, int, int, int, int, int, int
) to service_role;

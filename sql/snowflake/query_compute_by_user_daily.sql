select
    convert_timezone('UTC', start_time)::date as usage_date,
    user_name,
    warehouse_name,
    sum(credits_attributed_compute) as credits_used
from snowflake.account_usage.query_attribution_history
where convert_timezone('UTC', start_time)::date >= dateadd(
    day,
    -%(window_days)s,
    convert_timezone('UTC', current_timestamp())::date
)
  and convert_timezone('UTC', start_time)::date < convert_timezone('UTC', current_timestamp())::date
  and warehouse_name is not null
  and user_name is not null
group by
    usage_date,
    user_name,
    warehouse_name
order by
    usage_date,
    user_name,
    warehouse_name

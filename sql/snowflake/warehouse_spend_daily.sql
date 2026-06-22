select
    convert_timezone('UTC', start_time)::date as usage_date,
    warehouse_name,
    sum(credits_used) as credits_used,
    sum(credits_used_compute) as credits_used_compute,
    sum(credits_attributed_compute_queries) as credits_attributed_queries
from snowflake.account_usage.warehouse_metering_history
where convert_timezone('UTC', start_time)::date >= dateadd(
    day,
    -%(window_days)s,
    convert_timezone('UTC', current_timestamp())::date
)
  and convert_timezone('UTC', start_time)::date
      < convert_timezone('UTC', current_timestamp())::date
group by
    usage_date,
    warehouse_name
order by
    usage_date,
    warehouse_name

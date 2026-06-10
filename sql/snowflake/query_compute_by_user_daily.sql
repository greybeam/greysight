select
    convert_timezone('UTC', attribution.start_time)::date as usage_date,
    query_history.user_name,
    attribution.warehouse_name,
    sum(attribution.credits_attributed_compute_queries) as credits_used
from snowflake.account_usage.query_attribution_history as attribution
inner join snowflake.account_usage.query_history as query_history
    on attribution.query_id = query_history.query_id
where convert_timezone('UTC', attribution.start_time)::date >= dateadd(
    day,
    -%(window_days)s,
    convert_timezone('UTC', current_timestamp())::date
)
  and convert_timezone('UTC', attribution.start_time)::date < convert_timezone('UTC', current_timestamp())::date
  and attribution.warehouse_name is not null
  and query_history.user_name is not null
group by
    usage_date,
    query_history.user_name,
    attribution.warehouse_name
order by
    usage_date,
    query_history.user_name,
    attribution.warehouse_name

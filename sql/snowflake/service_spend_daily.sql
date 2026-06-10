select
    usage_date,
    service_type,
    sum(credits_used) as credits_used
from snowflake.account_usage.metering_daily_history
where usage_date >= dateadd(
    day,
    -%(window_days)s,
    convert_timezone('UTC', current_timestamp())::date
)
  and usage_date < convert_timezone('UTC', current_timestamp())::date
group by
    usage_date,
    service_type
order by
    usage_date,
    service_type

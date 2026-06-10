select
    usage_date,
    database_name,
    avg(average_database_bytes) as average_database_bytes,
    avg(average_failsafe_bytes) as average_failsafe_bytes
from snowflake.account_usage.database_storage_usage_history
where usage_date >= dateadd(
    day,
    -%(window_days)s,
    convert_timezone('UTC', current_timestamp())::date
)
  and usage_date < convert_timezone('UTC', current_timestamp())::date
group by
    usage_date,
    database_name
order by
    usage_date,
    database_name
